from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import TYPE_CHECKING

import websockets
from websockets.server import ServerConnection

from core.config import Config
from core.ipc_protocol import (
    ChatEvent,
    RPCResponse,
    INVALID_REQUEST,
    INTERNAL_ERROR,
    parse_message,
    IPCError,
)

if TYPE_CHECKING:
    from core.agent import Agent
    from core.memory import PARAStore

logger = logging.getLogger(__name__)


async def run_daemon(
    config: Config,
    agent: "Agent | None" = None,
    store: "PARAStore | None" = None,
    agent_name: str = "assistant",
) -> None:
    """Start the WebSocket IPC server and run until cancelled."""

    async def _handle_connection(websocket: ServerConnection) -> None:
        client = websocket.remote_address
        logger.info("Client connected: %s", client)
        try:
            async for raw_message in websocket:
                logger.debug("Received: %s", raw_message)
                await _dispatch(websocket, str(raw_message))
        except websockets.exceptions.ConnectionClosedOK:
            pass
        except websockets.exceptions.ConnectionClosedError as e:
            logger.warning("Connection closed with error: %s", e)
        finally:
            logger.info("Client disconnected: %s", client)

    async def _dispatch(websocket: ServerConnection, raw: str) -> None:
        try:
            request = parse_message(raw)
        except IPCError as exc:
            error_resp = RPCResponse.err(id="null", code=INVALID_REQUEST, message=str(exc))
            await websocket.send(error_resp.model_dump_json())
            return

        if request.method == "chat":
            if agent is None:
                err = RPCResponse.err(request.id, INTERNAL_ERROR, "Agent not initialized")
                await websocket.send(err.model_dump_json())
                return

            text_parts: list[str] = []
            try:
                async for event in agent.run(request.params.message):
                    chat_event = ChatEvent(params=event)
                    await websocket.send(chat_event.model_dump_json())
                    if event.get("type") == "text":
                        text_parts.append(event["content"])
            except Exception as exc:
                logger.exception("Agent error: %s", exc)
                err = RPCResponse.err(request.id, INTERNAL_ERROR, f"Agent error: {exc}")
                await websocket.send(err.model_dump_json())
                return

            _archive_session(config, store, agent_name, request.params.message, text_parts)
            await websocket.send(RPCResponse.ok(request.id, {"status": "complete"}).model_dump_json())

    port = config.daemon.port
    logger.info("Starting daemon on ws://127.0.0.1:%d", port)

    async with websockets.serve(_handle_connection, "127.0.0.1", port):
        logger.info("Daemon ready.")
        await asyncio.get_running_loop().create_future()  # run forever


def _archive_session(
    config: Config,
    store: "PARAStore | None",
    agent_name: str,
    user_message: str,
    text_parts: list[str],
) -> None:
    """Archive the session summary and update recent_tasks.md if configured."""
    if store is None or not config.memory.session_auto_archive:
        return
    today = date.today().isoformat()
    response_text = "\n".join(text_parts)
    summary = f"## User\n{user_message}\n\n## Assistant\n{response_text}"
    try:
        store.archive_session(agent_name, summary)
        if user_message:
            store.append_recent_task(agent_name, today, [user_message[:200]])
    except Exception as exc:
        logger.warning("Session archiving failed: %s", exc)
