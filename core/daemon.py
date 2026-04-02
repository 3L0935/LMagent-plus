from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from datetime import date
from typing import TYPE_CHECKING

import websockets
from websockets.server import ServerConnection

from core.config import Config
from core.ipc_protocol import (
    ChatEvent,
    RPCResponse,
    INVALID_REQUEST,
    INVALID_PARAMS,
    INTERNAL_ERROR,
    parse_message,
    IPCError,
)

if TYPE_CHECKING:
    from core.agent import Agent
    from core.memory import PARAStore
    from core.runtime.llama_manager import LocalBackendManager

logger = logging.getLogger(__name__)


async def run_daemon(
    config: Config,
    agent: "Agent | None" = None,
    store: "PARAStore | None" = None,
    agent_name: str = "assistant",
    local_manager: "LocalBackendManager | None" = None,
) -> None:
    """Start the WebSocket IPC server and run until cancelled."""
    _start_time = time.monotonic()

    # Pending system notifications — populated by callbacks (e.g. idle unload)
    # and drained by the next "poll" request from any CLI client.
    _notification_queue: list[dict] = []

    def _push_notification(msg: str, level: str = "info") -> None:
        _notification_queue.append({"message": msg, "level": level})

    # Wire idle-unload callback so the CLI can display it
    if local_manager is not None:
        local_manager._on_unload = lambda name: _push_notification(
            f"{name} unloaded (idle timeout)", level="warning"
        )

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
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            error_resp = RPCResponse.err(id="null", code=INVALID_REQUEST, message=str(exc))
            await websocket.send(error_resp.model_dump_json())
            return

        method = data.get("method")
        req_id = data.get("id", "null")

        if method == "ping":
            model_name = ""
            if local_manager is not None and local_manager.is_loaded:
                model_name = config.backends.local.default_model
            await websocket.send(RPCResponse.ok(req_id, {
                "status": "ok",
                "agent": agent_name,
                "model": model_name,
                "uptime_seconds": int(time.monotonic() - _start_time),
            }).model_dump_json())
            return

        if method == "poll":
            notifications = list(_notification_queue)
            _notification_queue.clear()
            await websocket.send(
                RPCResponse.ok(req_id, {"notifications": notifications}).model_dump_json()
            )
            return

        if method == "chat":
            try:
                request = parse_message(raw)
            except IPCError as exc:
                error_resp = RPCResponse.err(id=req_id, code=INVALID_REQUEST, message=str(exc))
                await websocket.send(error_resp.model_dump_json())
                return

            if agent is None:
                err = RPCResponse.err(request.id, INTERNAL_ERROR, "Agent not initialized")
                await websocket.send(err.model_dump_json())
                return

            if local_manager is not None and not local_manager.is_loaded and config.routing.default in ("local", "auto"):
                model_name = config.backends.local.default_model or "local model"
                await websocket.send(
                    ChatEvent(params={"type": "status", "message": f"Loading {model_name}…"}).model_dump_json()
                )
                try:
                    await local_manager.ensure_loaded_from_config()
                except Exception as exc:
                    err = RPCResponse.err(request.id, INTERNAL_ERROR, f"Model load failed: {exc}")
                    await websocket.send(err.model_dump_json())
                    return
                await websocket.send(
                    ChatEvent(params={"type": "model_ready", "message": f"{model_name} loaded"}).model_dump_json()
                )

            text_parts: list[str] = []
            try:
                async for event in agent.run(request.params.message, model=request.params.model_id):
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
            return

        if method == "model.reload":
            model_id = data.get("params", {}).get("model_id", "")
            if not model_id:
                await websocket.send(
                    RPCResponse.err(req_id, INVALID_PARAMS, "model_id required").model_dump_json()
                )
                return
            if local_manager is None:
                await websocket.send(
                    RPCResponse.err(req_id, INTERNAL_ERROR, "Local backend not enabled").model_dump_json()
                )
                return
            try:
                from core.runtime.model_manager import get_model_path
                model_path = get_model_path(model_id)
                if model_path is None:
                    await websocket.send(
                        RPCResponse.err(
                            req_id, INTERNAL_ERROR, f"Model {model_id!r} not found — download it first"
                        ).model_dump_json()
                    )
                    return
                await local_manager.ensure_loaded(model_path)
                await websocket.send(
                    RPCResponse.ok(req_id, {"status": "loaded", "model": model_id}).model_dump_json()
                )
            except Exception as exc:
                await websocket.send(
                    RPCResponse.err(req_id, INTERNAL_ERROR, str(exc)).model_dump_json()
                )
            return

        if method == "daemon.restart":
            await websocket.send(RPCResponse.ok(req_id, {"status": "restarting"}).model_dump_json())
            async def _do_restart() -> None:
                await asyncio.sleep(0.3)
                os.execv(sys.executable, [sys.executable, "-m", "core"])
            asyncio.create_task(_do_restart())
            return

        error_resp = RPCResponse.err(id=req_id, code=INVALID_REQUEST, message=f"Unknown method: {method!r}")
        await websocket.send(error_resp.model_dump_json())

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
