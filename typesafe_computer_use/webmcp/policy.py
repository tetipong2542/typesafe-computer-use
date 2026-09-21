"""WebMCP Policy Engine & Schema Validation: Consequential actions and untrusted boundary enforcement."""

from __future__ import annotations

import json
import logging
from typing import Any

import jsonschema

from .models import WebMCPToolDefinition

logger = logging.getLogger("typesafe.webmcp.policy")

CONSEQUENTIAL_KEYWORDS = frozenset({
    "payment",
    "pay",
    "purchase",
    "buy",
    "checkout",
    "delete",
    "remove",
    "publish",
    "send",
    "transfer",
})


def is_consequential_tool(tool_def: WebMCPToolDefinition) -> bool:
    """Determine whether a tool performs high-impact / irreversible side effects."""
    if tool_def.annotations.consequential or tool_def.annotations.requires_approval:
        return True

    name_lower = tool_def.name.lower()
    return any(kw in name_lower for kw in CONSEQUENTIAL_KEYWORDS)


def validate_tool_arguments(tool_def: WebMCPToolDefinition, arguments: dict[str, Any]) -> tuple[bool, str | None]:
    """Validate invocation arguments against the tool's JSON Schema."""
    schema = tool_def.input_schema
    if not schema:
        return True, None

    # Filter out internal framework arguments like _user_approved
    clean_args = {k: v for k, v in arguments.items() if not k.startswith("_")}

    try:
        jsonschema.validate(instance=clean_args, schema=schema)
        return True, None
    except jsonschema.ValidationError as e:
        msg = f"Schema validation error for tool '{tool_def.name}': {e.message}"
        logger.warning(msg)
        return False, msg
    except jsonschema.SchemaError as e:
        msg = f"Invalid JSON Schema declared by website for tool '{tool_def.name}': {e.message}"
        logger.error(msg)
        return False, msg


def wrap_untrusted_output(raw_output: Any) -> str:
    """Wrap untrusted execution output from arbitrary websites in explicit security delimiters."""
    content = json.dumps(raw_output, indent=2, default=str) if isinstance(raw_output, (dict, list)) else str(raw_output)

    # Sanitize closing delimiters inside content to prevent boundary escaping
    escaped = content.replace("</untrusted_webmcp_output>", "[ESCAPED_DELIMITER]")
    return f"<untrusted_webmcp_output>\n{escaped}\n</untrusted_webmcp_output>"
