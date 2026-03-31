"""
Backend router — selects and calls the appropriate LLM backend (cloud or local).

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


class Router:
    def __init__(self, config: "Config") -> None:
        self._config = config

    async def chat_completion(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        stream: bool = False,
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
            raise NotImplementedError(
                "Local backend not yet implemented — Phase 1 required."
            )
        if backend == "cloud" or backend == "auto":
            return await self._cloud_completion(messages, tools)

        raise BackendError(f"Unknown backend: {backend!r}")

    async def _cloud_completion(
        self,
        messages: list[dict],
        tools: list[dict] | None,
    ) -> dict:
        """Route to the configured cloud provider."""
        cloud_cfg = self._config.backends.cloud

        # Prefer Anthropic if key present, else OpenAI
        anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
        openai_key = os.environ.get("OPENAI_API_KEY")

        if anthropic_key:
            return await self._anthropic_completion(messages, tools, cloud_cfg.anthropic.default_model, anthropic_key)
        if openai_key:
            return await self._openai_completion(messages, tools, cloud_cfg.openai.default_model, openai_key)

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
