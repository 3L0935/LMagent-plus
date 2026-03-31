"""Tests for core/tool_registry.py"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock

from core.tool_registry import ToolDefinition, ToolRegistry
from core.errors import ToolError


def _make_tool(name: str = "test_tool") -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=f"A test tool named {name}",
        input_schema={
            "type": "object",
            "properties": {
                "value": {"type": "string"},
                "count": {"type": "integer"},
            },
            "required": ["value"],
            "additionalProperties": False,
        },
        handler=AsyncMock(return_value={"ok": True}),
    )


class TestToolRegistryBasics:
    def test_register_and_get(self):
        registry = ToolRegistry()
        tool = _make_tool("my_tool")
        registry.register(tool)
        assert registry.get("my_tool") is tool

    def test_get_unknown_returns_none(self):
        registry = ToolRegistry()
        assert registry.get("nonexistent") is None

    def test_list_tools_empty(self):
        assert ToolRegistry().list_tools() == []

    def test_list_tools_returns_all(self):
        registry = ToolRegistry()
        registry.register(_make_tool("a"))
        registry.register(_make_tool("b"))
        names = {t.name for t in registry.list_tools()}
        assert names == {"a", "b"}

    def test_duplicate_registration_raises(self):
        registry = ToolRegistry()
        registry.register(_make_tool("dup"))
        with pytest.raises(ValueError, match="already registered"):
            registry.register(_make_tool("dup"))


class TestToolRegistryValidation:
    def test_valid_input_passes(self):
        registry = ToolRegistry()
        registry.register(_make_tool("t"))
        registry.validate_input("t", {"value": "hello"})  # should not raise

    def test_missing_required_field_raises_tool_error(self):
        registry = ToolRegistry()
        registry.register(_make_tool("t"))
        with pytest.raises(ToolError, match="Invalid input"):
            registry.validate_input("t", {})  # missing 'value'

    def test_wrong_type_raises_tool_error(self):
        registry = ToolRegistry()
        registry.register(_make_tool("t"))
        with pytest.raises(ToolError, match="Invalid input"):
            registry.validate_input("t", {"value": 123})  # should be string

    def test_additional_properties_raises_tool_error(self):
        registry = ToolRegistry()
        registry.register(_make_tool("t"))
        with pytest.raises(ToolError):
            registry.validate_input("t", {"value": "x", "unknown_field": True})

    def test_unknown_tool_raises_tool_error(self):
        registry = ToolRegistry()
        with pytest.raises(ToolError, match="Unknown tool"):
            registry.validate_input("ghost", {"value": "x"})


class TestToolRegistryApiFormat:
    def test_to_api_format_structure(self):
        registry = ToolRegistry()
        registry.register(_make_tool("mytool"))
        api = registry.to_api_format()

        assert len(api) == 1
        entry = api[0]
        assert entry["type"] == "function"
        assert entry["function"]["name"] == "mytool"
        assert "description" in entry["function"]
        assert "parameters" in entry["function"]

    def test_to_api_format_empty(self):
        assert ToolRegistry().to_api_format() == []

    def test_parameters_match_input_schema(self):
        tool = _make_tool("check_schema")
        registry = ToolRegistry()
        registry.register(tool)
        api = registry.to_api_format()
        assert api[0]["function"]["parameters"] == tool.input_schema
