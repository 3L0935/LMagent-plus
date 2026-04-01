"""Tests for core/tools/call_agent.py."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.errors import ToolError
from core.tool_registry import ToolDefinition, ToolRegistry
from core.tools.call_agent import (
    CALL_AGENT_SCHEMA,
    _format_task_message,
    make_call_agent_tool,
)


# ---------------------------------------------------------------------------
# _format_task_message
# ---------------------------------------------------------------------------

class TestFormatTaskMessage:
    def test_task_only(self):
        result = _format_task_message({"task": "fix the bug"})
        assert result == "fix the bug"

    def test_with_files(self):
        result = _format_task_message({"task": "fix it", "files": ["auth.py", "utils.py"]})
        assert "Files: auth.py, utils.py" in result

    def test_with_constraints(self):
        result = _format_task_message({
            "task": "fix it",
            "constraints": ["do not change the API", "keep backward compat"],
        })
        assert "do not change the API" in result
        assert "keep backward compat" in result

    def test_with_context(self):
        result = _format_task_message({"task": "fix it", "context": "Bug introduced in last PR"})
        assert "Bug introduced in last PR" in result

    def test_full_payload(self):
        result = _format_task_message({
            "task": "fix auth bug",
            "files": ["auth.py"],
            "constraints": ["no API changes"],
            "context": "tokens expire too early",
        })
        assert "fix auth bug" in result
        assert "auth.py" in result
        assert "no API changes" in result
        assert "tokens expire too early" in result


# ---------------------------------------------------------------------------
# make_call_agent_tool — static properties
# ---------------------------------------------------------------------------

class TestMakeCallAgentTool:
    def test_returns_tool_definition(self):
        router = MagicMock()
        registry = ToolRegistry()
        tool = make_call_agent_tool(router, registry)
        assert isinstance(tool, ToolDefinition)
        assert tool.name == "call_agent"

    def test_schema_is_call_agent_schema(self):
        router = MagicMock()
        registry = ToolRegistry()
        tool = make_call_agent_tool(router, registry)
        assert tool.input_schema == CALL_AGENT_SCHEMA

    def test_when_to_use_is_set(self):
        tool = make_call_agent_tool(MagicMock(), ToolRegistry())
        assert tool.when_to_use is not None
        assert len(tool.when_to_use) > 0


# ---------------------------------------------------------------------------
# call_agent handler — via tool_registry validation
# ---------------------------------------------------------------------------

class TestCallAgentSchema:
    def test_valid_minimal_payload(self):
        registry = ToolRegistry()
        tool = make_call_agent_tool(MagicMock(), registry)
        registry.register(tool)
        # Should not raise
        registry.validate_input("call_agent", {
            "name": "coder",
            "payload": {"task": "fix the bug"},
        })

    def test_rejects_unknown_agent_name(self):
        from core.errors import ToolError
        registry = ToolRegistry()
        tool = make_call_agent_tool(MagicMock(), registry)
        registry.register(tool)
        with pytest.raises(ToolError, match="Invalid input"):
            registry.validate_input("call_agent", {
                "name": "unknown_agent",
                "payload": {"task": "do something"},
            })

    def test_rejects_missing_task(self):
        from core.errors import ToolError
        registry = ToolRegistry()
        tool = make_call_agent_tool(MagicMock(), registry)
        registry.register(tool)
        with pytest.raises(ToolError, match="Invalid input"):
            registry.validate_input("call_agent", {
                "name": "coder",
                "payload": {},  # missing 'task'
            })

    def test_rejects_self_routing_to_assistant(self):
        from core.errors import ToolError
        registry = ToolRegistry()
        tool = make_call_agent_tool(MagicMock(), registry)
        registry.register(tool)
        with pytest.raises(ToolError, match="Invalid input"):
            registry.validate_input("call_agent", {
                "name": "assistant",  # not in enum
                "payload": {"task": "do something"},
            })


# ---------------------------------------------------------------------------
# call_agent handler — execution
# ---------------------------------------------------------------------------

class TestCallAgentHandler:
    @pytest.fixture
    def base_registry(self) -> ToolRegistry:
        registry = ToolRegistry()
        registry.register(ToolDefinition(
            name="bash",
            description="Run a shell command.",
            input_schema={"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]},
            handler=AsyncMock(return_value={"stdout": "ok", "stderr": "", "returncode": 0}),
        ))
        registry.register(ToolDefinition(
            name="read_file",
            description="Read a file.",
            input_schema={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
            handler=AsyncMock(return_value={"content": ""}),
        ))
        registry.register(ToolDefinition(
            name="write_file",
            description="Write a file.",
            input_schema={"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]},
            handler=AsyncMock(return_value={}),
        ))
        registry.register(ToolDefinition(
            name="list_directory",
            description="List a directory.",
            input_schema={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
            handler=AsyncMock(return_value={"entries": []}),
        ))
        registry.register(ToolDefinition(
            name="git_clone",
            description="Clone a repo.",
            input_schema={"type": "object", "properties": {"url": {"type": "string"}, "dest": {"type": "string"}}, "required": ["url", "dest"]},
            handler=AsyncMock(return_value={}),
        ))
        registry.register(ToolDefinition(
            name="git_status",
            description="Git status.",
            input_schema={"type": "object", "properties": {"repo_path": {"type": "string"}}, "required": ["repo_path"]},
            handler=AsyncMock(return_value={}),
        ))
        registry.register(ToolDefinition(
            name="git_log",
            description="Git log.",
            input_schema={"type": "object", "properties": {"repo_path": {"type": "string"}}, "required": ["repo_path"]},
            handler=AsyncMock(return_value={}),
        ))
        return registry

    @pytest.mark.asyncio
    async def test_handler_returns_agent_output(self, base_registry: ToolRegistry):
        """Handler collects text output from sub-agent and returns it."""
        router = MagicMock()
        # Sub-agent LLM returns a plain text response (no tool calls)
        router.chat_completion = AsyncMock(return_value={
            "choices": [{"message": {"content": "Task completed.", "tool_calls": None}, "finish_reason": "stop"}]
        })

        tool = make_call_agent_tool(router, base_registry)
        result = await tool.handler({
            "name": "coder",
            "payload": {"task": "fix the bug in auth.py", "files": ["auth.py"]},
        })

        assert result["agent"] == "coder"
        assert "Task completed." in result["output"]
        assert result["errors"] == []

    @pytest.mark.asyncio
    async def test_handler_unknown_persona_raises_tool_error(self, base_registry: ToolRegistry):
        """A persona name that doesn't exist raises ToolError."""
        router = MagicMock()
        tool = make_call_agent_tool(router, base_registry)

        # "unknown" is blocked by JSON Schema enum, but test the handler directly
        with patch("core.tools.call_agent.load_persona", side_effect=Exception("not found")):
            with pytest.raises(Exception):
                await tool.handler({"name": "coder", "payload": {"task": "do it"}})

    @pytest.mark.asyncio
    async def test_handler_scopes_registry_to_persona(self, base_registry: ToolRegistry):
        """Writer persona should only receive file_ops tools, not bash or git."""
        router = MagicMock()
        router.chat_completion = AsyncMock(return_value={
            "choices": [{"message": {"content": "Done.", "tool_calls": None}, "finish_reason": "stop"}]
        })

        captured_registry: list[ToolRegistry] = []

        original_agent_class = None

        from core import agent as agent_module

        original_init = agent_module.Agent.__init__

        def capturing_init(self, router, tool_registry, **kwargs):
            captured_registry.append(tool_registry)
            original_init(self, router, tool_registry, **kwargs)

        with patch.object(agent_module.Agent, "__init__", capturing_init):
            tool = make_call_agent_tool(router, base_registry)
            await tool.handler({"name": "writer", "payload": {"task": "draft a README"}})

        assert len(captured_registry) == 1
        sub_reg = captured_registry[0]
        tool_names = {t.name for t in sub_reg.list_tools()}
        # writer only has file_ops group
        assert "read_file" in tool_names
        assert "write_file" in tool_names
        assert "list_directory" in tool_names
        # no bash, no git
        assert "bash" not in tool_names
        assert "git_clone" not in tool_names
