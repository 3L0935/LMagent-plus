from __future__ import annotations

import asyncio
import json
import logging
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

logger = logging.getLogger(__name__)

# Set by run_daemon after agent is initialized
_agent: "Agent | None" = None


async def _handle_connection(websocket: ServerConnection) -> None:
    """Dispatch JSON-RPC messages from a WebSocket client to the agent loop."""
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
    """Parse and route a single JSON-RPC message."""
    # Parse
    try:
        request = parse_message(raw)
    except IPCError as exc:
        error_resp = RPCResponse.err(id="null", code=INVALID_REQUEST, message=str(exc))
        await websocket.send(error_resp.model_dump_json())
        return

    # Dispatch chat method
    if request.method == "chat":
        if _agent is None:
            err = RPCResponse.err(request.id, INTERNAL_ERROR, "Agent not initialized")
            await websocket.send(err.model_dump_json())
            return

        try:
            async for event in _agent.run(request.params.message):
                chat_event = ChatEvent(params=event)
                await websocket.send(chat_event.model_dump_json())
        except Exception as exc:
            logger.exception("Agent error: %s", exc)
            err = RPCResponse.err(request.id, INTERNAL_ERROR, f"Agent error: {exc}")
            await websocket.send(err.model_dump_json())
            return

        await websocket.send(RPCResponse.ok(request.id, {"status": "complete"}).model_dump_json())


async def run_daemon(config: Config, agent: "Agent | None" = None) -> None:
    """Start the WebSocket IPC server and run until cancelled."""
    global _agent
    _agent = agent

    port = config.daemon.port
    logger.info("Starting daemon on ws://127.0.0.1:%d", port)

    async with websockets.serve(_handle_connection, "127.0.0.1", port):
        logger.info("Daemon ready.")
        await asyncio.get_running_loop().create_future()  # run forever
