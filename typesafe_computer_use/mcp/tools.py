"""Typed Pydantic schemas and contracts for WebMCP tools."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class BrowserNavigateInput(BaseModel):
    url: str = Field(..., description="Target HTTP or HTTPS URL to navigate active browser tab to")


class BrowserClickInput(BaseModel):
    target: str = Field(..., description="Element selector, test_id, visible text, or semantic identifier")
    role: str | None = Field(None, description="Accessible role (e.g. 'button', 'link', 'checkbox')")
    name: str | None = Field(None, description="Accessible name or label matching the role")
    test_id: str | None = Field(None, description="Explicit data-testid attribute value")


class BrowserFillInput(BaseModel):
    target: str = Field(..., description="Input element selector, placeholder, or accessible label")
    text: str = Field(..., description="Text content to fill into target element")


class BrowserGetDomInput(BaseModel):
    max_elements: int = Field(150, ge=1, le=500, description="Maximum number of DOM elements to summarize")
    max_depth: int = Field(6, ge=1, le=12, description="Maximum element tree traversal depth")


class BrowserVerifyInput(BaseModel):
    condition: str = Field(
        ...,
        description="Verification condition: 'element_present', 'visible', 'hidden', 'text_contains', 'value_equals', 'url_contains'",
    )
    target: str = Field(..., description="Target selector, element locator, or URL substring")
    expected_value: str | None = Field(None, description="Expected inner text or attribute value")


class BrowserStatusResult(BaseModel):
    url: str
    title: str
    is_connected: bool
    navigation_epoch: int
    cdp_port: int | None
    execution_gate_locked: bool


class BrowserToolResult(BaseModel):
    success: bool
    data: Any | None = None
    error: str | None = None
    side_effect_state: str | None = None
    navigation_epoch: int | None = None
