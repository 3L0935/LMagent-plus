"""
Tests for streaming paths — agent text_delta events, SSE parsing, archive capture.

Previously untested: chat_completion_stream, SSE tool-call accumulation,
text_delta vs text distinction in daemon session archive.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.agent import Agent
from core.tool_registry import ToolDefinition, ToolRegistry


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _collect(gen) -> list[dict]:
    events = []
    async for event in gen:
        events.append(event)
    return events


def _make_mock_router_streaming(chunks: list[dict]):
    """Return a router mock whose chat_completion_stream yields the given chunks."""
    router = MagicMock()

    async def _stream(*args, **kwargs):
        for chunk in chunks:
            yield chunk

    router.chat_completion_stream = _stream
    return router


def _make_echo_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(ToolDefinition(
        name="echo",
        description="Echo tool",
        input_schema={
            "type": "object",
            "properties": {"msg": {"type": "string"}},
            "required": ["msg"],
        },
        handler=AsyncMock(return_value={"echoed": "hello"}),
    ))
    return registry


def _sse_lines(*dicts: dict) -> list[str]:
    """Format dicts as SSE data lines."""
    lines = []
    for d in dicts:
        lines.append(f"data: {json.dumps(d)}")
    lines.append("data: [DONE]")
    return lines


# ── Agent streaming path ───────────────────────────────────────────────────────

class TestAgentStreamingTextOnly:
    @pytest.mark.asyncio
    async def test_text_delta_events_emitted(self):
        """Agent uses streaming path: text_delta events must be yielded."""
        router = _make_mock_router_streaming([
            {"type": "text_delta", "content": "Hello"},
            {"type": "text_delta", "content": " world"},
            {"type": "done"},
        ])
        agent = Agent(router=router, tool_registry=ToolRegistry())
        events = await _collect(agent.run("hi"))

        delta_events = [e for e in events if e["type"] == "text_delta"]
        assert len(delta_events) == 2
        assert delta_events[0]["content"] == "Hello"
        assert delta_events[1]["content"] == " world"
        assert events[-1]["type"] == "done"

    @pytest.mark.asyncio
    async def test_streaming_text_content_accumulated(self):
        """text_delta chunks are concatenated into the full response."""
        router = _make_mock_router_streaming([
            {"type": "text_delta", "content": "Part1"},
            {"type": "text_delta", "content": "Part2"},
            {"type": "done"},
        ])
        agent = Agent(router=router, tool_registry=ToolRegistry())
        events = await _collect(agent.run("hi"))

        delta_contents = "".join(e["content"] for e in events if e["type"] == "text_delta")
        assert delta_contents == "Part1Part2"

    @pytest.mark.asyncio
    async def test_streaming_fallback_on_error(self):
        """When streaming raises, agent falls back to non-streaming and yields text."""
        router = MagicMock()

        async def _failing_stream(*args, **kwargs):
            raise RuntimeError("stream broken")
            yield  # pragma: no cover

        router.chat_completion_stream = _failing_stream
        router.chat_completion = AsyncMock(return_value={
            "choices": [{"message": {"role": "assistant", "content": "Fallback response", "tool_calls": []}}]
        })

        agent = Agent(router=router, tool_registry=ToolRegistry())
        events = await _collect(agent.run("hi"))

        text_events = [e for e in events if e["type"] == "text"]
        assert any(e["content"] == "Fallback response" for e in text_events)
        assert events[-1]["type"] == "done"


class TestAgentStreamingToolCalls:
    @pytest.mark.asyncio
    async def test_streaming_tool_call_accumulation(self):
        """Streaming tool_calls chunk is processed and tool is executed."""
        tool_call = {
            "id": "call_1",
            "type": "function",
            "function": {"name": "echo", "arguments": '{"msg": "hello"}'},
        }
        call_count = 0

        async def _stream(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield {"type": "tool_calls", "tool_calls": [tool_call]}
                yield {"type": "done"}
            else:
                yield {"type": "text_delta", "content": "Done."}
                yield {"type": "done"}

        router = MagicMock()
        router.chat_completion_stream = _stream
        registry = _make_echo_registry()
        agent = Agent(router=router, tool_registry=registry)

        events = await _collect(agent.run("call echo"))

        tool_call_events = [e for e in events if e["type"] == "tool_call"]
        tool_result_events = [e for e in events if e["type"] == "tool_result"]
        assert len(tool_call_events) == 1
        assert tool_call_events[0]["name"] == "echo"
        assert len(tool_result_events) == 1
        assert events[-1]["type"] == "done"


# ── Router SSE parsing — local (llama-server) ─────────────────────────────────

class TestRouterLocalSSEParsing:
    @pytest.mark.asyncio
    async def test_local_stream_yields_text_delta(self):
        """_local_completion_stream correctly parses llama-server SSE text chunks."""
        from core.config import Config
        from core.router import Router

        chunks = [
            {"choices": [{"delta": {"content": "Hello"}, "finish_reason": None}]},
            {"choices": [{"delta": {"content": " world"}, "finish_reason": "stop"}]},
        ]

        mock_resp = MagicMock()
        mock_resp.status_code = 200

        async def _aiter_lines():
            for c in chunks:
                yield f"data: {json.dumps(c)}"
            yield "data: [DONE]"

        mock_resp.aiter_lines = _aiter_lines

        @asynccontextmanager
        async def _mock_stream(*args, **kwargs):
            yield mock_resp

        mock_client = MagicMock()
        mock_client.stream = _mock_stream

        config = Config()
        router = Router(config)
        router._client = mock_client

        results = []
        async for chunk in router._local_completion_stream(
            [{"role": "user", "content": "hi"}], None
        ):
            results.append(chunk)

        text_deltas = [r for r in results if r["type"] == "text_delta"]
        assert len(text_deltas) == 2
        assert text_deltas[0]["content"] == "Hello"
        assert text_deltas[1]["content"] == " world"
        assert results[-1]["type"] == "done"

    @pytest.mark.asyncio
    async def test_local_stream_accumulates_tool_calls(self):
        """Tool call fragments are accumulated across SSE chunks."""
        from core.config import Config
        from core.router import Router

        chunks = [
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_1", "type": "function", "function": {"name": "bash", "arguments": ""}}]}, "finish_reason": None}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": '{"cmd"'}}]}, "finish_reason": None}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": ': "ls"}'}}]}, "finish_reason": "tool_calls"}]},
        ]

        mock_resp = MagicMock()
        mock_resp.status_code = 200

        async def _aiter_lines():
            for c in chunks:
                yield f"data: {json.dumps(c)}"
            yield "data: [DONE]"

        mock_resp.aiter_lines = _aiter_lines

        @asynccontextmanager
        async def _mock_stream(*args, **kwargs):
            yield mock_resp

        mock_client = MagicMock()
        mock_client.stream = _mock_stream

        config = Config()
        router = Router(config)
        router._client = mock_client

        results = []
        async for chunk in router._local_completion_stream(
            [{"role": "user", "content": "list files"}], None
        ):
            results.append(chunk)

        tool_chunks = [r for r in results if r["type"] == "tool_calls"]
        assert len(tool_chunks) == 1
        tc = tool_chunks[0]["tool_calls"][0]
        assert tc["function"]["name"] == "bash"
        assert tc["function"]["arguments"] == '{"cmd": "ls"}'


# ── Router SSE parsing — Anthropic ────────────────────────────────────────────

class TestRouterAnthropicSSEParsing:
    @pytest.mark.asyncio
    async def test_anthropic_stream_yields_text_delta(self):
        """_anthropic_completion_stream correctly parses Anthropic SSE text chunks."""
        from core.config import Config
        from core.router import Router

        sse_events = [
            ("content_block_start", {"index": 0, "content_block": {"type": "text", "text": ""}}),
            ("content_block_delta", {"index": 0, "delta": {"type": "text_delta", "text": "Hello"}}),
            ("content_block_delta", {"index": 0, "delta": {"type": "text_delta", "text": " there"}}),
            ("content_block_stop", {"index": 0}),
            ("message_stop", {"type": "message_stop"}),
        ]

        mock_resp = MagicMock()
        mock_resp.status_code = 200

        async def _aiter_lines():
            for event_type, data in sse_events:
                yield f"event: {event_type}"
                yield f"data: {json.dumps(data)}"

        mock_resp.aiter_lines = _aiter_lines

        @asynccontextmanager
        async def _mock_stream(*args, **kwargs):
            yield mock_resp

        mock_client = MagicMock()
        mock_client.stream = _mock_stream

        config = Config()
        router = Router(config)
        router._client = mock_client

        results = []
        async for chunk in router._anthropic_completion_stream(
            [{"role": "user", "content": "hi"}], None, "claude-sonnet-4-6", "fake-key"
        ):
            results.append(chunk)

        text_deltas = [r for r in results if r["type"] == "text_delta"]
        assert len(text_deltas) == 2
        assert text_deltas[0]["content"] == "Hello"
        assert text_deltas[1]["content"] == " there"
        assert results[-1]["type"] == "done"

    @pytest.mark.asyncio
    async def test_anthropic_stream_accumulates_tool_use(self):
        """Anthropic input_json_delta events are accumulated into a single tool call."""
        from core.config import Config
        from core.router import Router

        sse_events = [
            ("content_block_start", {"index": 0, "content_block": {"type": "tool_use", "id": "toolu_01", "name": "bash", "input": {}}}),
            ("content_block_delta", {"index": 0, "delta": {"type": "input_json_delta", "partial_json": '{"cmd"'}}),
            ("content_block_delta", {"index": 0, "delta": {"type": "input_json_delta", "partial_json": ': "ls"}'}}),
            ("content_block_stop", {"index": 0}),
            ("message_stop", {"type": "message_stop"}),
        ]

        mock_resp = MagicMock()
        mock_resp.status_code = 200

        async def _aiter_lines():
            for event_type, data in sse_events:
                yield f"event: {event_type}"
                yield f"data: {json.dumps(data)}"

        mock_resp.aiter_lines = _aiter_lines

        @asynccontextmanager
        async def _mock_stream(*args, **kwargs):
            yield mock_resp

        mock_client = MagicMock()
        mock_client.stream = _mock_stream

        config = Config()
        router = Router(config)
        router._client = mock_client

        results = []
        async for chunk in router._anthropic_completion_stream(
            [{"role": "user", "content": "list files"}], None, "claude-sonnet-4-6", "fake-key"
        ):
            results.append(chunk)

        tool_chunks = [r for r in results if r["type"] == "tool_calls"]
        assert len(tool_chunks) == 1
        tc = tool_chunks[0]["tool_calls"][0]
        assert tc["function"]["name"] == "bash"
        assert tc["function"]["arguments"] == '{"cmd": "ls"}'


# ── Router SSE parsing — OpenAI ───────────────────────────────────────────────

class TestRouterOpenAISSEParsing:
    @pytest.mark.asyncio
    async def test_openai_stream_yields_text_delta(self):
        """_openai_completion_stream correctly parses OpenAI SSE text chunks."""
        from core.config import Config
        from core.router import Router

        chunks = [
            {"choices": [{"delta": {"content": "Hi"}, "finish_reason": None}]},
            {"choices": [{"delta": {"content": "!"}, "finish_reason": "stop"}]},
        ]

        mock_resp = MagicMock()
        mock_resp.status_code = 200

        async def _aiter_lines():
            for c in chunks:
                yield f"data: {json.dumps(c)}"
            yield "data: [DONE]"

        mock_resp.aiter_lines = _aiter_lines

        @asynccontextmanager
        async def _mock_stream(*args, **kwargs):
            yield mock_resp

        mock_client = MagicMock()
        mock_client.stream = _mock_stream

        config = Config()
        router = Router(config)
        router._client = mock_client

        results = []
        async for chunk in router._openai_completion_stream(
            [{"role": "user", "content": "hi"}], None, "gpt-4o", "fake-key"
        ):
            results.append(chunk)

        text_deltas = [r for r in results if r["type"] == "text_delta"]
        assert len(text_deltas) == 2
        assert "".join(t["content"] for t in text_deltas) == "Hi!"
        assert results[-1]["type"] == "done"


# ── Daemon archive: text_delta captured (BUG-3 regression) ───────────────────

class TestDaemonStreamingArchive:
    def test_text_delta_captured_in_archive(self):
        """_archive_session must collect text_delta events, not just text."""
        from unittest.mock import MagicMock
        from core.config import Config
        from core.daemon import _archive_session

        config = Config()
        config.memory.session_auto_archive = True

        store = MagicMock()

        # Simulate what daemon collects during a streaming session
        text_parts = ["Hello ", "world"]  # collected from text_delta events

        _archive_session(config, store, "assistant", "test message", text_parts)

        store.archive_session.assert_called_once()
        call_args = store.archive_session.call_args
        summary = call_args[0][1]  # second positional arg
        assert "Hello " in summary
        assert "world" in summary

    def test_empty_text_parts_produces_empty_assistant_section(self):
        """Empty text_parts (old bug: streaming session not collected) → empty archive."""
        from unittest.mock import MagicMock
        from core.config import Config
        from core.daemon import _archive_session

        config = Config()
        config.memory.session_auto_archive = True

        store = MagicMock()
        _archive_session(config, store, "assistant", "test message", [])

        store.archive_session.assert_called_once()
        call_args = store.archive_session.call_args
        summary = call_args[0][1]
        assert "## Assistant\n" in summary
        # Should be empty after the assistant header
        after_header = summary.split("## Assistant\n", 1)[1]
        assert after_header.strip() == ""
