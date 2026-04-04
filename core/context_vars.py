"""
Async context variables shared across the agent execution stack.

These are set by the daemon per-request and read by tool handlers
(e.g. call_agent) without requiring changes to intermediate signatures.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Awaitable, Callable

# Maps persona name → model_id override for the current chat request.
# Set by daemon.py before agent.run(); read by call_agent._handler.
persona_models_ctx: ContextVar[dict[str, str]] = ContextVar(
    "persona_models", default={}
)

# Callable set by daemon.py to allow call_agent to request a model
# selection from the client mid-stream.
#
# Signature: async (persona_name: str) -> str | None
#   Returns the chosen model_id, or None to use the default.
persona_setup_fn_ctx: ContextVar[
    Callable[[str], Awaitable[str | None]] | None
] = ContextVar("persona_setup_fn", default=None)
