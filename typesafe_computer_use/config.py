"""Tunables, the site catalog, and environment loading."""

from __future__ import annotations

import os
from pathlib import Path

MIN_OCR_CONFIDENCE = 0.3
MAX_OPTIONS = 255  # TypeSafe Choice ceiling
ABORT_CORNER_PX = 4
DEFAULT_MIN_CONFIDENCE = 0.4
DEFAULT_STEPS = 100
DEFAULT_DELAY = 2.0
DEFAULT_WRITER_MODEL = "claude-haiku-4-5"
DEFAULT_ANSWER_MODEL = "claude-sonnet-5"  # runs once per run, on a screenshot: worth a stronger reader
DEFAULT_BROWSER = "Google Chrome"

# Sites the classifier can pick by name. Anything else goes through the writer.
SITES: dict[str, str] = {
    "github": "https://github.com/",
    "gmail": "https://mail.google.com/",
    "google_calendar": "https://calendar.google.com/",
    "launchdarkly": "https://app.launchdarkly.com/",
    "linear": "https://linear.app/",
    "notion": "https://www.notion.so/",
    "slack": "https://app.slack.com/",
    "typesafe_console": "https://console.typesafe.ai/",
    "youtube": "https://www.youtube.com/",
}

# Supported model presets for OpenAI-compatible proxies
POPULAR_MODELS: dict[str, str] = {
    # ChatGPT Backend (via Codex authentication):
    "gpt-5.6-sol": "Flagship Sol model with advanced reasoning and vision (default)",
    "gpt-5.5": "Fast and lightweight model on ChatGPT backend",
    # Standard OpenAI / OpenRouter endpoints:
    "gpt-4o": "Standard balanced model for external OpenAI API",
    "gpt-4o-mini": "Ultra fast and cost-efficient model",
    "gpt-4.1-mini": "Lightweight and efficient model",
    "o3-mini": "High-speed reasoning model",
}


DEFAULT_OPENAI_MODEL = "gpt-5.6-sol"
DEFAULT_OPENAI_BASE_URL = "http://localhost:8888/v1"


def load_dotenv(path: Path) -> None:
    """Set KEY=VALUE lines from a .env file into the environment unless already set."""
    if not path.is_file():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def writer_provider() -> str:
    """Determine the active writer provider: 'openai', 'anthropic', or 'none'."""
    explicit = os.environ.get("WRITER_PROVIDER", "").lower()
    if explicit in ("openai", "anthropic"):
        return explicit
    if os.environ.get("OPENAI_BASE_URL") or os.environ.get("OPENAI_API_KEY"):
        return "openai"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    return "openai"


def openai_base_url() -> str:
    """Get the OpenAI-compatible proxy base URL."""
    return os.environ.get("OPENAI_BASE_URL", DEFAULT_OPENAI_BASE_URL)


def openai_api_key() -> str:
    """Get the OpenAI-compatible proxy API key."""
    return os.environ.get("OPENAI_API_KEY", "test")


def browser() -> str:
    return os.environ.get("CLICKER_BROWSER", DEFAULT_BROWSER)


def writer_model() -> str:
    if "CLICKER_WRITER_MODEL" in os.environ:
        return os.environ["CLICKER_WRITER_MODEL"]
    return DEFAULT_OPENAI_MODEL if writer_provider() == "openai" else DEFAULT_WRITER_MODEL


def answer_model() -> str:
    if "CLICKER_ANSWER_MODEL" in os.environ:
        return os.environ["CLICKER_ANSWER_MODEL"]
    return DEFAULT_OPENAI_MODEL if writer_provider() == "openai" else DEFAULT_ANSWER_MODEL


def email() -> str | None:
    return os.environ.get("CLICKER_EMAIL") or None

