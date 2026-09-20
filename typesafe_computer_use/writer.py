"""The writer model: the only place free text is generated, when the classifier asks for it and once when the run ends."""

from __future__ import annotations

import base64
import io
import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import anthropic
import openai
from PIL import Image

from .config import answer_model, openai_api_key, openai_base_url, writer_model, writer_provider
from .dates import now_context
from .models import Item, Screen
from .perception import near_field

ANSWER_IMAGE_EDGE = 1568  # the longest edge a vision model reads without shrinking the image itself


class OpenAIWriter:
    """Client adapter for OpenAI-compatible proxies (e.g. codex-openai-proxy)."""

    def __init__(self, client: openai.OpenAI):
        self.client = client

    def structured(
        self,
        system: str,
        packet: dict,
        properties: dict,
        max_tokens: int,
        model: str | None = None,
        image: Image.Image | None = None,
    ) -> dict:
        """Query OpenAI chat completions, embedding instructions for Codex backend compatibility."""
        target_model = model or writer_model()
        schema_instruction = (
            f"\n\nYou must reply strictly with a valid JSON object matching this schema properties: {json.dumps(properties)}.\n"
            "Output valid JSON only. Do not wrap in markdown or code fences."
        )
        prompt_text = f"[Instructions]\n{system}\n\n[Input Data]\n{json.dumps(packet)}{schema_instruction}"

        # Codex backend on ChatGPT rejects role: "system", so instructions are included in user content
        if image is not None:
            shrunk = image.convert("RGB")
            shrunk.thumbnail((ANSWER_IMAGE_EDGE, ANSWER_IMAGE_EDGE))
            buffer = io.BytesIO()
            shrunk.save(buffer, format="PNG")
            data = base64.b64encode(buffer.getvalue()).decode()
            content: list[dict] = [
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{data}"}},
                {"type": "text", "text": prompt_text},
            ]
        else:
            content = [{"type": "text", "text": prompt_text}]

        response = self.client.chat.completions.create(
            model=target_model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": content}],
        )

        if getattr(response, "error", None):
            error_msg = response.error.get("message") if isinstance(response.error, dict) else str(response.error)
            raise RuntimeError(f"OpenAI proxy error: {error_msg}")

        if not response.choices or not response.choices[0].message:
            raise RuntimeError(f"OpenAI proxy returned no choices in response: {response}")

        raw_text = (response.choices[0].message.content or "").strip()
        return _parse_json(raw_text)



def _parse_json(raw: str) -> dict:
    """Extract and parse JSON object safely from raw model output."""
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()

    try:
        return json.loads(cleaned)
    except Exception:
        # Fallback: extract the outermost JSON object
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(cleaned[start : end + 1])
        raise


def make_writer() -> Any | None:
    """Create a writer client: OpenAI-compatible proxy or Anthropic client."""
    provider = writer_provider()
    if provider == "openai":
        try:
            client = openai.OpenAI(base_url=openai_base_url(), api_key=openai_api_key())
            return OpenAIWriter(client)
        except Exception as e:
            print(f"Warning: failed to initialize OpenAI client: {e}")
            return None

    if provider == "anthropic":
        try:
            client = anthropic.Anthropic()
            if client.api_key or getattr(client, "auth_token", None):
                return client
        except Exception:
            pass

    return None


def _structured(
    writer: Any,
    system: str,
    packet: dict,
    properties: dict,
    max_tokens: int,
    model: str | None = None,
    image: Image.Image | None = None,
) -> dict:
    if isinstance(writer, OpenAIWriter):
        return writer.structured(system, packet, properties, max_tokens, model=model, image=image)

    # Anthropic or mock fake writer
    content: list[dict] = [{"type": "text", "text": json.dumps(packet)}]
    if image is not None:
        content.insert(0, _image_block(image))
    response = writer.messages.create(
        model=model or writer_model(),
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": content}],
        output_config={
            "format": {
                "type": "json_schema",
                "schema": {
                    "type": "object",
                    "properties": properties,
                    "required": list(properties),
                    "additionalProperties": False,
                },
            }
        },
    )
    return json.loads("".join(b.text for b in response.content if b.type == "text"))



def _image_block(image: Image.Image) -> dict:
    """The capture as a PNG the model can read. PNG because screen text does not survive JPEG well."""
    shrunk = image.convert("RGB")
    shrunk.thumbnail((ANSWER_IMAGE_EDGE, ANSWER_IMAGE_EDGE))
    buffer = io.BytesIO()
    shrunk.save(buffer, format="PNG")
    data = base64.b64encode(buffer.getvalue()).decode()
    return {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": data}}


def compose_text(writer: Any, goal: str, screen: Screen, items: list[Item], history: list[str]) -> str:
    """The exact string to type into the focused field. Empty means the writer declined."""
    packet = {
        "goal": goal,
        "now": now_context(),
        "frontmost_app": screen.app,
        "previous_actions": history[-8:],
        "focused_field": screen.field.summary() if screen.field else None,
        "text_near_field": near_field(screen, items),
        "all_screen_text": [it.text for it in items][:120],
    }
    data = _structured(
        writer,
        system=(
            "You fill in one text field on a user's screen. You receive the user's goal, recent "
            "actions, the focused field's label and placeholder, and nearby screen text. Decide the "
            "exact string to type. Never invent credentials, passwords, or personal data; for such "
            "fields, or when the field should not be filled, set fill to false."
        ),
        packet=packet,
        properties={"fill": {"type": "boolean"}, "text": {"type": "string"}, "reason": {"type": "string"}},
        max_tokens=256,
    )
    return data["text"].strip() if data["fill"] else ""


def valid_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" and "." in parsed.netloc and not any(ch.isspace() for ch in url)


def compose_url(writer: Any, goal: str, history: list[str]) -> str:
    """The URL to open for this goal. Empty means no sensible site, or an invalid proposal."""
    data = _structured(
        writer,
        system=(
            "Given a user's goal for their web browser, give the single best https URL to open first. "
            "Prefer the site's homepage or the most direct public page. If no website is implied, set ok to false."
        ),
        packet={"goal": goal, "now": now_context(), "previous_actions": history[-8:]},
        properties={"ok": {"type": "boolean"}, "url": {"type": "string"}, "reason": {"type": "string"}},
        max_tokens=200,
    )
    url = data["url"].strip() if data["ok"] else ""
    return url if valid_url(url) else ""


@dataclass(frozen=True)
class Answer:
    text: str
    achieved: bool  # whether the screen itself shows the goal reached, in the writer's judgement


def compose_answer(
    writer: Any, goal: str, screen: Screen, items: list[Item], history: list[str], stopped: str
) -> Answer:
    """What to tell the user now that the run is over: the result when the screen holds it, where things stand when not.

    The classifier can stop on the right page but cannot say what the page says. The writer reads the
    capture itself as well as its text, since OCR misreads a letter here and there and drops layout.
    """
    packet = {
        "goal": goal,
        "now": now_context(),
        "why_the_run_stopped": stopped,
        "actions_taken": history,
        "frontmost_app": screen.app,
        "browser_active_tab_url": screen.url,
        "screen_text_in_reading_order": [it.text for it in items],
    }
    data = _structured(
        writer,
        system=(
            "An agent drove a user's computer toward the user's goal and has now stopped. You receive "
            "the goal, the actions it took, why it stopped, a capture of the screen as it is now, and "
            "the text read from that screen. Tell the user the result. When the goal asks for "
            "information, lead with that information, taken only from the screen: never from memory, "
            "and never a guess. When the goal asks for something to be done, say whether the screen "
            "shows it done. When the screen does not hold the result, say so plainly, then say what is "
            "on screen and the one next step that would get there. Trust the capture over the text "
            "where the two disagree. Plain text, no markdown, four sentences at most. Set achieved to "
            "true only when the screen itself shows the goal reached."
        ),
        packet=packet,
        properties={"achieved": {"type": "boolean"}, "answer": {"type": "string"}},
        max_tokens=1024,
        model=answer_model(),
        image=screen.image,
    )
    return Answer(text=data["answer"].strip(), achieved=data["achieved"])
