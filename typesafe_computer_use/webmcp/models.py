"""Data models and schemas for Native WebMCP (W3C/webmcp.com draft specification)."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel, Field


class ToolAnnotation(BaseModel):
    """Semantic annotations declaring tool characteristics per WebMCP spec."""

    read_only: bool = Field(False, description="Whether the tool is free of persistent side-effects (readOnlyHint)")
    consequential: bool = Field(False, description="Whether the tool performs high-impact actions like payment, delete (consequentialHint)")
    requires_approval: bool = Field(False, description="Whether human approval is explicitly required by site policy")


class WebMCPToolDefinition(BaseModel):
    """Specification of an in-page tool declared by a website via document.modelContext or HTML forms."""

    name: str = Field(..., description="Unique tool identifier within the page scope")
    description: str = Field("", description="Untrusted human/agent readable summary of tool functionality")
    input_schema: dict[str, Any] = Field(default_factory=dict, description="JSON Schema defining required and optional arguments")
    annotations: ToolAnnotation = Field(default_factory=ToolAnnotation, description="Tool safety annotations")
    origin: str = Field(..., description="Document origin (e.g. https://store.example.com) to ensure origin isolation")
    navigation_epoch: int = Field(0, description="Navigation epoch counter when tool was declared")
    schema_hash: str = Field("", description="SHA256 fingerprint of canonical input schema")
    source: str = Field("imperative", description="Registration source: 'imperative' (JS API) or 'declarative_form' (HTML form)")

    def compute_schema_hash(self) -> str:
        """Compute stable SHA256 hash of the canonical input schema."""
        canonical = json.dumps(self.input_schema, sort_keys=True)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]

    def model_post_init(self, __context: Any) -> None:
        if not self.schema_hash:
            self.schema_hash = self.compute_schema_hash()


class WebMCPInvocationResult(BaseModel):
    """Result of invoking an in-page WebMCP tool."""

    tool_name: str
    success: bool
    data: Any | None = None
    error: str | None = None
    side_effect_state: str = "not_started"
    duration_ms: float = 0.0
    untrusted_output: str | None = None
    origin: str = ""
    navigation_epoch: int = 0
