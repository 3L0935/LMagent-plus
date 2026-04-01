"""
Backend router — selects and calls the appropriate LLM backend (cloud or local).
Agent router — maps a task string to a specialized agent name (heuristic, no ML).

Cloud backends (Anthropic, OpenAI) are implemented here.
Local backend (llama-server) is stubbed — added after Phase 1 merges.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, AsyncGenerator

import httpx

from core.errors import BackendError

if TYPE_CHECKING:
    from core.config import Config
    from core.runtime.llama_manager import LocalBackendManager


class Router:
    def __init__(
        self,
        config: "Config",
        local_manager: "LocalBackendManager | None" = None,
    ) -> None:
        self._config = config
        self._local_manager = local_manager

    async def chat_completion(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        stream: bool = False,
        model: str | None = None,
    ) -> dict:
        """
        Route a chat completion request to the configured backend.

        Args:
            messages: List of {"role": ..., "content": ...} dicts.
            tools: Optional list of tools in OpenAI format.
            stream: Streaming not yet implemented — raises NotImplementedError.

        Returns:
            Raw response dict from the backend.

        Raises:
            BackendError: On API errors or missing credentials.
            NotImplementedError: For local backend or stream=True.
        """
        if stream:
            raise NotImplementedError("Streaming is not yet implemented.")

        backend = self._config.routing.default

        if backend == "local":
            await self._jit_load()
            return await self._local_completion(messages, tools)
        if backend == "cloud":
            return await self._cloud_completion(messages, tools, model_override=model)
        if backend == "auto":
            try:
                await self._jit_load()
                return await self._local_completion(messages, tools)
            except (BackendError, OSError):
                if not self._config.routing.auto_fallback:
                    raise
                return await self._cloud_completion(messages, tools, model_override=model)

        raise BackendError(f"Unknown backend: {backend!r}")

    async def _jit_load(self) -> None:
        """Trigger JIT model load if a LocalBackendManager is wired in."""
        if self._local_manager is not None:
            await self._local_manager.ensure_loaded_from_config()

    async def _local_completion(
        self,
        messages: list[dict],
        tools: list[dict] | None,
    ) -> dict:
        """Call llama-server's OpenAI-compatible API."""
        local_cfg = self._config.backends.local
        body: dict = {"messages": messages}
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"

        try:
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(
                    f"http://127.0.0.1:{local_cfg.port}/v1/chat/completions",
                    json=body,
                )
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise BackendError(
                f"Cannot reach llama-server on port {local_cfg.port}. "
                "Is the daemon running with a local model loaded?"
            ) from exc

        if resp.status_code != 200:
            raise BackendError(f"llama-server error {resp.status_code}: {resp.text}")
        return resp.json()

    async def _cloud_completion(
        self,
        messages: list[dict],
        tools: list[dict] | None,
        model_override: str | None = None,
    ) -> dict:
        """Route to the configured cloud provider."""
        cloud_cfg = self._config.backends.cloud

        # Prefer Anthropic if key present, else OpenAI
        anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
        openai_key = os.environ.get("OPENAI_API_KEY")

        if anthropic_key:
            model = model_override or cloud_cfg.anthropic.default_model
            return await self._anthropic_completion(messages, tools, model, anthropic_key)
        if openai_key:
            model = model_override or cloud_cfg.openai.default_model
            return await self._openai_completion(messages, tools, model, openai_key)

        raise BackendError(
            "No cloud API key found. Set ANTHROPIC_API_KEY or OPENAI_API_KEY."
        )

    async def _anthropic_completion(
        self,
        messages: list[dict],
        tools: list[dict] | None,
        model: str,
        api_key: str,
    ) -> dict:
        """Call Anthropic Messages API."""
        # Anthropic uses a separate system message
        system = None
        filtered = []
        for msg in messages:
            if msg["role"] == "system":
                system = msg["content"]
            else:
                filtered.append(msg)

        body: dict = {"model": model, "max_tokens": 4096, "messages": filtered}
        if system:
            body["system"] = system
        if tools:
            # Convert OpenAI format → Anthropic format
            body["tools"] = [
                {
                    "name": t["function"]["name"],
                    "description": t["function"]["description"],
                    "input_schema": t["function"]["parameters"],
                }
                for t in tools
            ]

        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json=body,
            )
            if resp.status_code != 200:
                raise BackendError(f"Anthropic API error {resp.status_code}: {resp.text}")
            raw = resp.json()

        # Normalize to OpenAI-like response format
        return _normalize_anthropic(raw)

    async def _openai_completion(
        self,
        messages: list[dict],
        tools: list[dict] | None,
        model: str,
        api_key: str,
    ) -> dict:
        """Call OpenAI Chat Completions API."""
        body: dict = {"model": model, "messages": messages}
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"

        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
            if resp.status_code != 200:
                raise BackendError(f"OpenAI API error {resp.status_code}: {resp.text}")
            return resp.json()


# ---------------------------------------------------------------------------
# Agent router (heuristic, keyword-based)
# ---------------------------------------------------------------------------

# Each rule is (keywords, agent_name). First match wins.
# This is the fallback strategy — the preferred approach is explicit call_agent()
# tool-calls from the orchestrator's system prompt.
_ROUTING_RULES: list[tuple[frozenset[str], str]] = [
    (
        frozenset({
            "code", "bug", "function", "class", "module", "file", "git",
            "patch", "refactor", "implement", "fix", "debug", "test", "compile",
        }),
        "coder",
    ),
    (
        frozenset({
            "research", "analyze", "analysis", "compare", "synthesis", "synthesize",
            "review", "explain", "investigate", "evaluate", "assess",
        }),
        "research",
    ),
    (
        frozenset({
            "write", "draft", "edit", "rephrase", "proofread", "document",
            "article", "blog", "summarize", "rewrite", "essay",
        }),
        "writer",
    ),
]


class AgentRouter:
    """
    Heuristic agent router — keyword-based, no ML.

    Maps a task string to an agent name by scanning for known keywords.
    Returns "assistant" when no rule matches.

    This is the fallback routing strategy. The preferred approach is explicit
    tool-call routing via call_agent() in the orchestrator's system prompt,
    which does not rely on keyword matching.
    """

    def route(self, task: str) -> str:
        """
        Return the best-matching agent name for the given task string.

        Args:
            task: Free-text task description from the user.

        Returns:
            Agent name: "coder", "writer", "research", or "assistant".
        """
        task_lower = task.lower()
        for keywords, agent in _ROUTING_RULES:
            if any(kw in task_lower for kw in keywords):
                return agent
        return "assistant"


# ---------------------------------------------------------------------------

def _normalize_anthropic(raw: dict) -> dict:
    """
    Convert Anthropic response to an OpenAI-compatible structure.

    OpenAI choice format:
      {"message": {"role": "assistant", "content": str | None,
                   "tool_calls": [{"id": ..., "type": "function",
                                   "function": {"name": ..., "arguments": str}}]}}
    """
    content_blocks = raw.get("content", [])
    text_parts = []
    tool_calls = []

    for i, block in enumerate(content_blocks):
        if block.get("type") == "text":
            text_parts.append(block["text"])
        elif block.get("type") == "tool_use":
            import json
            tool_calls.append({
                "id": block.get("id", f"call_{i}"),
                "type": "function",
                "function": {
                    "name": block["name"],
                    "arguments": json.dumps(block.get("input", {})),
                },
            })

    message: dict = {"role": "assistant", "content": "\n".join(text_parts) or None}
    if tool_calls:
        message["tool_calls"] = tool_calls

    return {
        "id": raw.get("id", ""),
        "model": raw.get("model", ""),
        "choices": [{"message": message, "finish_reason": raw.get("stop_reason", "stop")}],
        "usage": raw.get("usage", {}),
    }
