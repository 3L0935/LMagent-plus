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
from core.context_vars import persona_models_ctx, persona_setup_fn_ctx
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

DEFAULT_PERSONA = "assistant"


async def run_daemon(
    config: Config,
    agents: "dict[str, Agent]",
    store: "PARAStore | None" = None,
    local_manager: "LocalBackendManager | None" = None,
) -> None:
    """Start the WebSocket IPC server and run until cancelled.

    Routes each chat request to the Agent matching request.params.agent_id,
    falling back to DEFAULT_PERSONA if the requested persona is not loaded.
    """
    _start_time = time.monotonic()

    # Pending system notifications — populated by callbacks (e.g. idle unload)
    # and drained by the next "poll" request from any CLI client.
    _notification_queue: list[dict] = []

    def _push_notification(msg: str, level: str = "info") -> None:
        _notification_queue.append({"message": msg, "level": level})

    if local_manager is not None:
        local_manager._on_unload = lambda name: _push_notification(
            f"{name} unloaded (idle timeout)", level="warning"
        )

    async def _handle_connection(websocket: ServerConnection) -> None:
        client = websocket.remote_address
        logger.info("Client connected: %s", client)

        # Inbox queue enables mid-stream bidirectional communication:
        # a reader task feeds all incoming messages here, while the chat
        # handler can also consume messages (e.g. persona.model.confirm).
        inbox: asyncio.Queue[str | None] = asyncio.Queue()

        async def _reader() -> None:
            try:
                async for raw_message in websocket:
                    await inbox.put(str(raw_message))
            except websockets.exceptions.ConnectionClosedOK:
                pass
            except websockets.exceptions.ConnectionClosedError as e:
                logger.warning("Connection closed with error: %s", e)
            finally:
                await inbox.put(None)  # sentinel — outer loop stops

        reader_task = asyncio.create_task(_reader())
        try:
            while True:
                raw = await inbox.get()
                if raw is None:
                    break  # connection closed
                logger.debug("Received: %s", raw)
                await _dispatch(websocket, raw, inbox)
        finally:
            reader_task.cancel()
            await asyncio.gather(reader_task, return_exceptions=True)
            logger.info("Client disconnected: %s", client)

    async def _dispatch(
        websocket: ServerConnection,
        raw: str,
        inbox: "asyncio.Queue[str | None]",
    ) -> None:
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
                "agents": list(agents.keys()),
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

            # Route to the requested persona; fall back to DEFAULT_PERSONA
            agent_id = request.params.agent_id or DEFAULT_PERSONA
            agent = agents.get(agent_id) or agents.get(DEFAULT_PERSONA)
            if agent is None:
                agent = next(iter(agents.values()), None)
            if agent is None:
                err = RPCResponse.err(request.id, INTERNAL_ERROR, "No agents loaded")
                await websocket.send(err.model_dump_json())
                return
            # Use the actual resolved name for archiving
            resolved_id = agent_id if agent_id in agents else DEFAULT_PERSONA

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

            # Thread per-persona model overrides into tool handlers via contextvar
            persona_models_ctx.set(request.params.persona_models)

            # Mid-stream model-selection: futures resolved when client sends
            # persona.model.confirm; shared with the side-channel task below.
            _setup_futures: dict[str, "asyncio.Future[str | None]"] = {}

            async def _setup_fn(persona_name: str) -> "str | None":
                """Emit persona_setup_required and wait for client to confirm."""
                from core.persona_loader import load_persona as _lp
                try:
                    _p = _lp(persona_name)
                    _default = _p.get("default_model") or ""
                    _cloud    = _p.get("cloud_equivalent") or ""
                except Exception:
                    _default = _cloud = ""

                fut: "asyncio.Future[str | None]" = asyncio.get_event_loop().create_future()
                _setup_futures[persona_name] = fut
                await websocket.send(ChatEvent(params={
                    "type": "persona_setup_required",
                    "persona": persona_name,
                    "default_model": _default,
                    "cloud_equivalent": _cloud,
                }).model_dump_json())
                try:
                    return await asyncio.wait_for(asyncio.shield(fut), timeout=120.0)
                except asyncio.TimeoutError:
                    logger.warning("persona_setup timeout for %s — using default", persona_name)
                    _setup_futures.pop(persona_name, None)
                    return None

            persona_setup_fn_ctx.set(_setup_fn)

            async def _side_channel() -> None:
                """Consume persona.model.confirm messages from inbox mid-stream."""
                while True:
                    raw_mid = await inbox.get()
                    if raw_mid is None:
                        # Connection closed — put sentinel back for outer loop
                        await inbox.put(None)
                        return
                    try:
                        msg = json.loads(raw_mid)
                    except json.JSONDecodeError:
                        continue
                    if msg.get("method") == "persona.model.confirm":
                        params  = msg.get("params", {})
                        persona = params.get("persona", "")
                        model   = params.get("model_id") or None
                        fut     = _setup_futures.pop(persona, None)
                        if fut and not fut.done():
                            fut.set_result(model)
                    # Discard any other mid-stream messages (polling etc.)

            text_parts: list[str] = []
            side_task = asyncio.create_task(_side_channel())
            try:
                async for event in agent.run(request.params.message, model=request.params.model_id):
                    chat_event = ChatEvent(params=event)
                    await websocket.send(chat_event.model_dump_json())
                    if event.get("type") in ("text", "text_delta"):
                        text_parts.append(event["content"])
            except Exception as exc:
                logger.exception("Agent error: %s", exc)
                err = RPCResponse.err(request.id, INTERNAL_ERROR, f"Agent error: {exc}")
                await websocket.send(err.model_dump_json())
                return
            finally:
                side_task.cancel()
                await asyncio.gather(side_task, return_exceptions=True)

            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None, _archive_session, config, store, resolved_id, request.params.message, text_parts
            )
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
