"""
JSON-RPC 2.0 message types for the WebSocket IPC.

All messages over the WebSocket use JSON-RPC 2.0.
CLI, GUI, and web all use the same message types defined here.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from core.errors import IPCError


# ── Inbound (client → daemon) ─────────────────────────────────────────────────

class ChatParams(BaseModel):
    message: str
    agent_id: str | None = None
    model_id: str | None = None
    persona_models: dict[str, str] = {}


class ChatRequest(BaseModel):
    jsonrpc: Literal["2.0"] = "2.0"
    method: Literal["chat"]
    params: ChatParams
    id: str


# ── Outbound (daemon → client) ────────────────────────────────────────────────

class ChatEvent(BaseModel):
    """Streamed event during agent execution."""
    jsonrpc: Literal["2.0"] = "2.0"
    method: Literal["chat.event"] = "chat.event"
    params: dict  # {"type": "text"|"tool_call"|"tool_result"|"done"|"error", ...}


class RPCResponse(BaseModel):
    """Final JSON-RPC response (success or error)."""
    jsonrpc: Literal["2.0"] = "2.0"
    result: dict | None = None
    error: dict | None = None
    id: str

    @classmethod
    def ok(cls, id: str, result: dict) -> "RPCResponse":
        return cls(id=id, result=result)

    @classmethod
    def err(cls, id: str, code: int, message: str, data: Any = None) -> "RPCResponse":
        error: dict = {"code": code, "message": message}
        if data is not None:
            error["data"] = data
        return cls(id=id, error=error)


# ── Standard JSON-RPC error codes ────────────────────────────────────────────

PARSE_ERROR      = -32700
INVALID_REQUEST  = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS   = -32602
INTERNAL_ERROR   = -32603


# ── Parsing ───────────────────────────────────────────────────────────────────

def parse_message(raw: str) -> ChatRequest:
    """
    Parse and validate an incoming JSON-RPC message.

    Returns:
        A validated ChatRequest.

    Raises:
        IPCError: If the message is malformed, not valid JSON, or has unknown method.
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise IPCError(f"JSON parse error: {exc}") from exc

    method = data.get("method")
    if method == "chat":
        try:
            return ChatRequest.model_validate(data)
        except ValidationError as exc:
            raise IPCError(f"Invalid chat request: {exc}") from exc

    raise IPCError(f"Unknown method: {method!r}")
