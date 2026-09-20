"""Tests for OpenAI proxy writer integration."""

from types import SimpleNamespace

from PIL import Image

from typesafe_computer_use.config import POPULAR_MODELS
from typesafe_computer_use.writer import OpenAIWriter, _parse_json


class MockOpenAIClient:
    """Mock client for testing OpenAIWriter without live network calls."""

    def __init__(self, response_text: str):
        self.response_text = response_text
        self.calls: list[dict] = []
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self._create)
        )

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        choice = SimpleNamespace(
            index=0,
            message=SimpleNamespace(role="assistant", content=self.response_text),
            finish_reason="stop",
        )
        return SimpleNamespace(choices=[choice])


def test_parse_json_variants():
    # Pure JSON
    assert _parse_json('{"status": "ok"}') == {"status": "ok"}

    # Wrapped in markdown json fences
    assert _parse_json('```json\n{"status": "ok"}\n```') == {"status": "ok"}

    # Text before or after JSON
    assert _parse_json('Here is your answer:\n{"status": "ok"}\nHope that helps!') == {"status": "ok"}


def test_openai_writer_structured_call():
    mock_client = MockOpenAIClient('{"fill": true, "text": "reaction nene", "reason": "search query"}')
    writer = OpenAIWriter(mock_client)

    result = writer.structured(
        system="Fill the search input",
        packet={"goal": "search for reaction nene"},
        properties={"fill": {"type": "boolean"}, "text": {"type": "string"}},
        max_tokens=256,
        model="gpt-5.6-sol",
    )

    assert result["fill"] is True
    assert result["text"] == "reaction nene"
    assert len(mock_client.calls) == 1
    call = mock_client.calls[0]
    assert call["model"] == "gpt-5.6-sol"
    # Instructions embedded in user message (no role: system for Codex compatibility)
    assert call["messages"][0]["role"] == "user"
    assert "Fill the search input" in call["messages"][0]["content"][0]["text"]


def test_openai_writer_with_image():
    mock_client = MockOpenAIClient('{"achieved": true, "answer": "Video found and playing."}')
    writer = OpenAIWriter(mock_client)

    img = Image.new("RGB", (100, 100), color="blue")
    result = writer.structured(
        system="Inspect screen",
        packet={"goal": "verify playback"},
        properties={"achieved": {"type": "boolean"}, "answer": {"type": "string"}},
        max_tokens=512,
        model="gpt-5.6-sol",
        image=img,
    )

    assert result["achieved"] is True
    call = mock_client.calls[0]
    content = call["messages"][0]["content"]
    assert content[0]["type"] == "image_url"
    assert content[0]["image_url"]["url"].startswith("data:image/png;base64,")


def test_popular_models_catalog():
    assert "gpt-5.6-sol" in POPULAR_MODELS
    assert "gpt-5.5" in POPULAR_MODELS
    assert "gpt-4o-mini" in POPULAR_MODELS

