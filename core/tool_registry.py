"""
Tool registry with strict JSON Schema validation.

Tools are registered with a name, description, JSON Schema for input validation,
and an async handler callable. The registry validates inputs before dispatch and
can serialize tools to OpenAI API format for injection into LLM calls.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

import jsonschema

from core.errors import ToolError


@dataclass
class ToolDefinition:
    name: str
    description: str
    input_schema: dict  # JSON Schema (type: object)
    handler: Callable[[dict], Any]  # async callable


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, tool: ToolDefinition) -> None:
        """Register a tool. Raises ValueError if name already registered."""
        if tool.name in self._tools:
            raise ValueError(f"Tool '{tool.name}' is already registered")
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)

    def list_tools(self) -> list[ToolDefinition]:
        return list(self._tools.values())

    def validate_input(self, tool_name: str, input_data: dict) -> None:
        """Validate input against the tool's JSON Schema. Raises ToolError on failure."""
        tool = self._tools.get(tool_name)
        if tool is None:
            raise ToolError(f"Unknown tool: '{tool_name}'")
        try:
            jsonschema.validate(input_data, tool.input_schema)
        except jsonschema.ValidationError as e:
            raise ToolError(f"Invalid input for tool '{tool_name}': {e.message}") from e

    def to_api_format(self) -> list[dict]:
        """Serialize all tools to OpenAI function-calling format."""
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.input_schema,
                },
            }
            for tool in self._tools.values()
        ]
