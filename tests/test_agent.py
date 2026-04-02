"""
Tests for core/agent.py — mocked LLM responses, tool call parsing, loop behavior.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.agent import Agent
from core.config import Config
from core.errors import ToolError
from core.router import Router
from core.tool_registry import ToolDefinition, ToolRegistry


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_text_response(content: str, finish_reason: str = "stop") -> dict:
    """Build an OpenAI-format response with a plain text message."""
    return {
        "choices": [{
            "message": {"role": "assistant", "content": content, "tool_calls": []},
            "finish_reason": finish_reason,
        }]
    }


def _make_tool_call_response(tool_name: str, args: dict, call_id: str = "call_1") -> dict:
    """Build an OpenAI-format response with a single tool call."""
    return {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": call_id,
                    "type": "function",
                    "function": {"name": tool_name, "arguments": json.dumps(args)},
                }],
            },
            "finish_reason": "tool_calls",
        }]
    }


def _make_registry_with_tool(
    name: str = "echo",
    handler_return: dict | None = None,
    raises: Exception | None = None,
) -> ToolRegistry:
    registry = ToolRegistry()
    if raises:
        handler = AsyncMock(side_effect=raises)
    else:
        handler = AsyncMock(return_value=handler_return or {"result": "ok"})

    registry.register(ToolDefinition(
        name=name,
        description="An echo tool",
        input_schema={
            "type": "object",
            "properties": {"msg": {"type": "string"}},
            "required": ["msg"],
        },
        handler=handler,
    ))
    return registry


async def _collect(gen) -> list[dict]:
    events = []
    async for event in gen:
        events.append(event)
    return events


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestAgentTextOnlyResponse:
    @pytest.mark.asyncio
    async def test_text_response_yields_text_and_done(self):
        router = MagicMock()
        router.chat_completion = AsyncMock(return_value=_make_text_response("Hello!"))
        registry = ToolRegistry()
        agent = Agent(router=router, tool_registry=registry)

        events = await _collect(agent.run("Hi"))

        types = [e["type"] for e in events]
        assert "text" in types
        assert types[-1] == "done"
        text_events = [e for e in events if e["type"] == "text"]
        assert text_events[0]["content"] == "Hello!"

    @pytest.mark.asyncio
    async def test_llm_error_yields_error_and_stops(self):
        router = MagicMock()
        router.chat_completion = AsyncMock(side_effect=Exception("network failure"))
        agent = Agent(router=router, tool_registry=ToolRegistry())

        events = await _collect(agent.run("test"))

        types = [e["type"] for e in events]
        assert "error" in types
        assert types[-1] == "done"


class TestAgentToolCallFlow:
    @pytest.mark.asyncio
    async def test_single_tool_call_then_text(self):
        """LLM calls a tool once, then returns text."""
        call_sequence = [
            _make_tool_call_response("echo", {"msg": "hello"}),
            _make_text_response("Done."),
        ]
        router = MagicMock()
        router.chat_completion = AsyncMock(side_effect=call_sequence)
        registry = _make_registry_with_tool("echo", {"echoed": "hello"})
        agent = Agent(router=router, tool_registry=registry)

        events = await _collect(agent.run("say hello"))

        types = [e["type"] for e in events]
        assert "tool_call" in types
        assert "tool_result" in types
        assert "text" in types
        assert types[-1] == "done"

        tool_call_evt = next(e for e in events if e["type"] == "tool_call")
        assert tool_call_evt["name"] == "echo"
        assert tool_call_evt["input"] == {"msg": "hello"}

    @pytest.mark.asyncio
    async def test_tool_result_fed_back_to_llm(self):
        """Verify the tool result is added to the conversation history."""
        call_sequence = [
            _make_tool_call_response("echo", {"msg": "test"}),
            _make_text_response("Got it."),
        ]
        router = MagicMock()
        router.chat_completion = AsyncMock(side_effect=call_sequence)
        registry = _make_registry_with_tool("echo", {"data": "value"})
        agent = Agent(router=router, tool_registry=registry)

        await _collect(agent.run("run tool"))

        # Second call should have tool result in messages
        second_call_messages = router.chat_completion.call_args_list[1][1]["messages"]
        roles = [m["role"] for m in second_call_messages]
        assert "tool" in roles


class TestAgentErrorHandling:
    @pytest.mark.asyncio
    async def test_unknown_tool_name_yields_error_no_crash(self):
        """Agent gracefully handles a call to a non-existent tool."""
        router = MagicMock()
        router.chat_completion = AsyncMock(side_effect=[
            _make_tool_call_response("nonexistent_tool", {"msg": "x"}),
            _make_text_response("Recovered."),
        ])
        agent = Agent(router=router, tool_registry=ToolRegistry())

        events = await _collect(agent.run("call missing tool"))

        types = [e["type"] for e in events]
        assert "error" in types
        assert types[-1] == "done"

    @pytest.mark.asyncio
    async def test_tool_raises_tool_error_yields_error_no_crash(self):
        router = MagicMock()
        router.chat_completion = AsyncMock(side_effect=[
            _make_tool_call_response("echo", {"msg": "hi"}),
            _make_text_response("Handled."),
        ])
        registry = _make_registry_with_tool("echo", raises=ToolError("boom"))
        agent = Agent(router=router, tool_registry=registry)

        events = await _collect(agent.run("trigger error"))

        types = [e["type"] for e in events]
        assert "error" in types
        assert types[-1] == "done"

    @pytest.mark.asyncio
    async def test_max_iterations_respected(self):
        """Agent stops after max_iterations even if LLM keeps returning tool calls."""
        # Always return a tool call — never text-only
        router = MagicMock()
        router.chat_completion = AsyncMock(
            return_value=_make_tool_call_response("echo", {"msg": "loop"})
        )
        registry = _make_registry_with_tool("echo")
        agent = Agent(router=router, tool_registry=registry, max_iterations=3)

        events = await _collect(agent.run("infinite loop"))

        assert router.chat_completion.call_count == 3
        types = [e["type"] for e in events]
        assert "error" in types  # max iterations error
        assert types[-1] == "done"


class TestAgentSystemPromptHooks:
    @pytest.mark.asyncio
    async def test_hooks_included_in_system_prompt(self):
        router = MagicMock()
        router.chat_completion = AsyncMock(return_value=_make_text_response("ok"))
        agent = Agent(
            router=router,
            tool_registry=ToolRegistry(),
            system_prompt_hooks=[
                lambda: "You are a helpful assistant.",
                lambda: "Always be concise.",
            ],
        )

        await _collect(agent.run("test"))

        messages = router.chat_completion.call_args[1]["messages"]
        system_msg = next((m for m in messages if m["role"] == "system"), None)
        assert system_msg is not None
        assert "You are a helpful assistant." in system_msg["content"]
        assert "Always be concise." in system_msg["content"]

    @pytest.mark.asyncio
    async def test_no_hooks_no_system_message(self):
        router = MagicMock()
        router.chat_completion = AsyncMock(return_value=_make_text_response("ok"))
        agent = Agent(router=router, tool_registry=ToolRegistry(), system_prompt_hooks=[])

        await _collect(agent.run("test"))

        messages = router.chat_completion.call_args[1]["messages"]
        roles = [m["role"] for m in messages]
        assert "system" not in roles


class TestAgentStreaming:
    @pytest.mark.asyncio
    async def test_streaming_yields_text_start_delta_end(self):
        """When router streams, agent emits text_start + text_delta + text_end."""

        async def _mock_stream(messages, tools=None, model=None):
            yield {"type": "text_delta", "content": "hello "}
            yield {"type": "text_delta", "content": "world"}
            yield {"type": "done"}

        router = MagicMock()
        router.chat_completion_stream = _mock_stream
        agent = Agent(router=router, tool_registry=ToolRegistry())

        events = await _collect(agent.run("test"))
        types = [e["type"] for e in events]

        assert "text_start" in types
        assert "text_delta" in types
        assert "text_end" in types
        assert types[-1] == "done"

        deltas = [e["content"] for e in events if e["type"] == "text_delta"]
        assert "".join(deltas) == "hello world"

    @pytest.mark.asyncio
    async def test_streaming_fallback_on_error(self):
        """When streaming fails, agent falls back to non-streaming chat_completion."""

        async def _failing_stream(messages, tools=None, model=None):
            raise RuntimeError("stream not available")
            yield  # make it a generator

        router = MagicMock()
        router.chat_completion_stream = _failing_stream
        router.chat_completion = AsyncMock(return_value=_make_text_response("fallback text"))
        agent = Agent(router=router, tool_registry=ToolRegistry())

        events = await _collect(agent.run("test"))
        types = [e["type"] for e in events]

        # Fallback emits text event (non-streaming) and done
        assert "text" in types
        assert types[-1] == "done"
        text_events = [e for e in events if e["type"] == "text"]
        assert text_events[0]["content"] == "fallback text"
