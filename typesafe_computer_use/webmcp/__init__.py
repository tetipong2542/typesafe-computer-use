"""Native WebMCP package for in-page website tool integration."""

from .adapter import NativeWebMCPAdapter
from .discovery import WebMCPDiscoveryService
from .models import ToolAnnotation, WebMCPInvocationResult, WebMCPToolDefinition
from .policy import is_consequential_tool, validate_tool_arguments, wrap_untrusted_output

__all__ = [
    "NativeWebMCPAdapter",
    "ToolAnnotation",
    "WebMCPDiscoveryService",
    "WebMCPInvocationResult",
    "WebMCPToolDefinition",
    "is_consequential_tool",
    "validate_tool_arguments",
    "wrap_untrusted_output",
]
