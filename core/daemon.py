from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import websockets
from websockets.server import ServerConnection

from core.config import Config

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


async def _handle_connection(websocket: ServerConnection) -> None:
    """Echo handler — skeleton for Phase 2 IPC dispatch."""
    client = websocket.remote_address
    logger.info("Client connected: %s", client)
    try:
        async for message in websocket:
            logger.debug("Received: %s", message)
            await websocket.send(message)  # echo
    except websockets.exceptions.ConnectionClosedOK:
        pass
    except websockets.exceptions.ConnectionClosedError as e:
        logger.warning("Connection closed with error: %s", e)
    finally:
        logger.info("Client disconnected: %s", client)


async def run_daemon(config: Config) -> None:
    """Start the WebSocket IPC server and run until cancelled."""
    port = config.daemon.port
    logger.info("Starting daemon on ws://127.0.0.1:%d", port)

    async with websockets.serve(_handle_connection, "127.0.0.1", port):
        logger.info("Daemon ready.")
        await asyncio.get_running_loop().create_future()  # run forever
