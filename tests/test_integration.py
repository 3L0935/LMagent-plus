"""
Minimal integration test — daemon ping + chat round-trip.

Starts a real daemon in an asyncio.Task, connects via WebSocket,
and verifies the ping and chat flows work end to end.

The LLM router is mocked to return a fixed text response so no
real model or API key is needed.
"""

from __future__ import annotations

import asyncio
import json
import socket
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import websockets

from core.config import Config
from core.daemon import run_daemon


def _free_port() -> int:
    """Find a free TCP port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _mock_agent(response_text: str = "hello from mock"):
    """Build a minimal mock Agent that yields text + done."""

    async def _run(user_message: str, model=None) -> AsyncGenerator[dict, None]:
        yield {"type": "text", "content": response_text}
        yield {"type": "done"}

    agent = MagicMock()
    agent.run = _run
    return agent


@pytest.mark.asyncio
async def test_ping_returns_ok():
    port = _free_port()
    config = Config()
    config.daemon.port = port

    agents = {"assistant": _mock_agent()}
    daemon_task = asyncio.create_task(
        run_daemon(config, agents=agents)
    )
    # Give the daemon a moment to bind
    await asyncio.sleep(0.1)

    try:
        async with websockets.connect(f"ws://127.0.0.1:{port}") as ws:
            await ws.send(json.dumps({"jsonrpc": "2.0", "method": "ping", "id": "1"}))
            raw = await asyncio.wait_for(ws.recv(), timeout=2)
            data = json.loads(raw)

        assert data["result"]["status"] == "ok"
        assert "uptime_seconds" in data["result"]
        assert "assistant" in data["result"]["agents"]
    finally:
        daemon_task.cancel()
        try:
            await daemon_task
        except (asyncio.CancelledError, Exception):
            pass


@pytest.mark.asyncio
async def test_chat_round_trip():
    port = _free_port()
    config = Config()
    config.daemon.port = port

    agents = {"assistant": _mock_agent("integration test response")}
    daemon_task = asyncio.create_task(
        run_daemon(config, agents=agents)
    )
    await asyncio.sleep(0.1)

    try:
        async with websockets.connect(f"ws://127.0.0.1:{port}") as ws:
            chat_request = {
                "jsonrpc": "2.0",
                "method": "chat",
                "params": {"message": "test message", "agent_id": "assistant", "model_id": None},
                "id": "req-1",
            }
            await ws.send(json.dumps(chat_request))

            events = []
            final_response = None
            for _ in range(10):
                raw = await asyncio.wait_for(ws.recv(), timeout=2)
                data = json.loads(raw)
                if "method" in data and data["method"] == "chat.event":
                    events.append(data["params"])
                elif "result" in data:
                    final_response = data
                    break

        event_types = [e["type"] for e in events]
        assert "text" in event_types
        assert "done" in event_types

        text_events = [e for e in events if e["type"] == "text"]
        assert any("integration test response" in e.get("content", "") for e in text_events)

        assert final_response is not None
        assert final_response["result"]["status"] == "complete"
    finally:
        daemon_task.cancel()
        try:
            await daemon_task
        except (asyncio.CancelledError, Exception):
            pass
