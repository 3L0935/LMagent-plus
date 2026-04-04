"""
Backend router — selects and calls the appropriate LLM backend (cloud or local).
Agent router — maps a task string to a specialized agent name (heuristic, no ML).

Cloud backends (Anthropic, OpenAI) are implemented here.
Local backend (llama-server) is stubbed — added after Phase 1 merges.
"""

from __future__ import annotations

import json
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
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Return the shared httpx client, creating it lazily."""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=120)
        return self._client

    async def close(self) -> None:
        """Close the persistent httpx client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

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
            raise NotImplementedError("Use chat_completion_stream() for streaming.")

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

    async def chat_completion_stream(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        model: str | None = None,
    ) -> AsyncGenerator[dict, None]:
        """
        Streaming chat completion — yields dicts as tokens arrive.

        Yields:
            {"type": "text_delta", "content": "..."}  — one or more text tokens
            {"type": "tool_calls", "tool_calls": [...]}  — accumulated tool calls (once)
            {"type": "done"}  — end of stream

        Raises:
            BackendError, NotImplementedError
        """
        backend = self._config.routing.default

        if backend == "local":
            await self._jit_load()
            async for chunk in self._local_completion_stream(messages, tools):
                yield chunk
        elif backend == "cloud":
            async for chunk in self._cloud_completion_stream(messages, tools, model_override=model):
                yield chunk
        elif backend == "auto":
            try:
                await self._jit_load()
                async for chunk in self._local_completion_stream(messages, tools):
                    yield chunk
            except (BackendError, OSError):
                if not self._config.routing.auto_fallback:
                    raise
                async for chunk in self._cloud_completion_stream(messages, tools, model_override=model):
                    yield chunk
        else:
            raise BackendError(f"Unknown backend: {backend!r}")

    async def _local_completion_stream(
        self,
        messages: list[dict],
        tools: list[dict] | None,
    ) -> AsyncGenerator[dict, None]:
        """Stream from llama-server's OpenAI-compatible SSE endpoint."""
        local_cfg = self._config.backends.local
        body: dict = {"messages": messages, "stream": True}
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"

        client = await self._get_client()
        try:
            async with client.stream(
                "POST",
                f"http://127.0.0.1:{local_cfg.port}/v1/chat/completions",
                json=body,
                timeout=120,
            ) as resp:
                if resp.status_code != 200:
                    content = await resp.aread()
                    raise BackendError(f"llama-server error {resp.status_code}: {content.decode()}")

                tool_calls_acc: dict[int, dict] = {}  # index → accumulated tool call
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data_str = line[6:]
                    if data_str.strip() == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    delta = chunk.get("choices", [{}])[0].get("delta", {})
                    if delta.get("content"):
                        yield {"type": "text_delta", "content": delta["content"]}

                    for tc_delta in delta.get("tool_calls", []):
                        idx = tc_delta.get("index", 0)
                        if idx not in tool_calls_acc:
                            tool_calls_acc[idx] = {"id": "", "type": "function", "function": {"name": "", "arguments": ""}}
                        acc = tool_calls_acc[idx]
                        if tc_delta.get("id"):
                            acc["id"] = tc_delta["id"]
                        fn = tc_delta.get("function", {})
                        if fn.get("name"):
                            acc["function"]["name"] += fn["name"]
                        if fn.get("arguments"):
                            acc["function"]["arguments"] += fn["arguments"]

                if tool_calls_acc:
                    yield {"type": "tool_calls", "tool_calls": list(tool_calls_acc.values())}
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise BackendError(
                f"Cannot reach llama-server on port {local_cfg.port}."
            ) from exc

        yield {"type": "done"}

    async def _cloud_completion_stream(
        self,
        messages: list[dict],
        tools: list[dict] | None,
        model_override: str | None = None,
    ) -> AsyncGenerator[dict, None]:
        """Route to the configured cloud provider for streaming."""
        cloud_cfg = self._config.backends.cloud
        anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
        openai_key = os.environ.get("OPENAI_API_KEY")

        if anthropic_key:
            model = model_override or cloud_cfg.anthropic.default_model
            async for chunk in self._anthropic_completion_stream(messages, tools, model, anthropic_key):
                yield chunk
        elif openai_key:
            model = model_override or cloud_cfg.openai.default_model
            async for chunk in self._openai_completion_stream(messages, tools, model, openai_key):
                yield chunk
        else:
            raise BackendError("No cloud API key found. Set ANTHROPIC_API_KEY or OPENAI_API_KEY.")

    async def _anthropic_completion_stream(
        self,
        messages: list[dict],
        tools: list[dict] | None,
        model: str,
        api_key: str,
    ) -> AsyncGenerator[dict, None]:
        """Stream from Anthropic Messages API (SSE)."""
        system, converted = _convert_messages_for_anthropic(messages)
        body: dict = {"model": model, "max_tokens": 4096, "messages": converted, "stream": True}
        if system:
            body["system"] = system
        if tools:
            body["tools"] = [
                {
                    "name": t["function"]["name"],
                    "description": t["function"]["description"],
                    "input_schema": t["function"]["parameters"],
                }
                for t in tools
            ]

        client = await self._get_client()
        tool_calls_by_index: dict[int, dict] = {}
        current_tool_index: int | None = None

        async with client.stream(
            "POST",
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json=body,
            timeout=120,
        ) as resp:
            if resp.status_code != 200:
                content = await resp.aread()
                raise BackendError(f"Anthropic API error {resp.status_code}: {content.decode()}")

            event_type: str = ""
            async for line in resp.aiter_lines():
                if line.startswith("event: "):
                    event_type = line[7:].strip()
                elif line.startswith("data: "):
                    try:
                        data = json.loads(line[6:])
                    except json.JSONDecodeError:
                        continue

                    if event_type == "content_block_start":
                        block = data.get("content_block", {})
                        if block.get("type") == "tool_use":
                            idx = data.get("index", 0)
                            current_tool_index = idx
                            tool_calls_by_index[idx] = {
                                "id": block.get("id", ""),
                                "type": "function",
                                "function": {"name": block.get("name", ""), "arguments": ""},
                            }

                    elif event_type == "content_block_delta":
                        delta = data.get("delta", {})
                        if delta.get("type") == "text_delta":
                            yield {"type": "text_delta", "content": delta.get("text", "")}
                        elif delta.get("type") == "input_json_delta" and current_tool_index is not None:
                            tool_calls_by_index[current_tool_index]["function"]["arguments"] += delta.get("partial_json", "")

                    elif event_type == "content_block_stop":
                        current_tool_index = None

                    elif event_type == "message_stop":
                        break

        if tool_calls_by_index:
            yield {"type": "tool_calls", "tool_calls": list(tool_calls_by_index.values())}
        yield {"type": "done"}

    async def _openai_completion_stream(
        self,
        messages: list[dict],
        tools: list[dict] | None,
        model: str,
        api_key: str,
    ) -> AsyncGenerator[dict, None]:
        """Stream from OpenAI Chat Completions API (SSE)."""
        body: dict = {"model": model, "messages": messages, "stream": True}
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"

        client = await self._get_client()
        tool_calls_acc: dict[int, dict] = {}

        async with client.stream(
            "POST",
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=120,
        ) as resp:
            if resp.status_code != 200:
                content = await resp.aread()
                raise BackendError(f"OpenAI API error {resp.status_code}: {content.decode()}")

            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data_str = line[6:]
                if data_str.strip() == "[DONE]":
                    break
                try:
                    chunk = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                delta = chunk.get("choices", [{}])[0].get("delta", {})
                if delta.get("content"):
                    yield {"type": "text_delta", "content": delta["content"]}

                for tc_delta in delta.get("tool_calls", []):
                    idx = tc_delta.get("index", 0)
                    if idx not in tool_calls_acc:
                        tool_calls_acc[idx] = {"id": "", "type": "function", "function": {"name": "", "arguments": ""}}
                    acc = tool_calls_acc[idx]
                    if tc_delta.get("id"):
                        acc["id"] = tc_delta["id"]
                    fn = tc_delta.get("function", {})
                    if fn.get("name"):
                        acc["function"]["name"] += fn["name"]
                    if fn.get("arguments"):
                        acc["function"]["arguments"] += fn["arguments"]

        if tool_calls_acc:
            yield {"type": "tool_calls", "tool_calls": list(tool_calls_acc.values())}
        yield {"type": "done"}

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
            client = await self._get_client()
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
        system, converted = _convert_messages_for_anthropic(messages)
        body: dict = {"model": model, "max_tokens": 4096, "messages": converted}
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

        client = await self._get_client()
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

        client = await self._get_client()
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

def _merge_consecutive_roles(messages: list[dict]) -> list[dict]:
    """
    Merge consecutive messages with the same role into one.

    Anthropic requires strict user/assistant alternation.
    Consecutive same-role messages are merged by combining their content.
    """
    if not messages:
        return messages
    merged: list[dict] = [messages[0]]
    for msg in messages[1:]:
        last = merged[-1]
        if msg["role"] == last["role"]:
            # Combine content — normalize both to lists of content blocks
            last_content = last["content"]
            new_content = msg["content"]
            if isinstance(last_content, str):
                last_content = [{"type": "text", "text": last_content}]
            if isinstance(new_content, str):
                new_content = [{"type": "text", "text": new_content}]
            merged[-1] = {"role": last["role"], "content": last_content + new_content}
        else:
            merged.append(msg)
    return merged


def _convert_messages_for_anthropic(
    messages: list[dict],
) -> tuple[str | None, list[dict]]:
    """
    Convert OpenAI-format messages to Anthropic format.

    Handles:
    - system messages → extracted separately (returned as first element)
    - tool role messages → converted to user messages with tool_result content blocks
    - assistant messages with tool_calls → converted to assistant messages with tool_use blocks
    """
    system: str | None = None
    converted: list[dict] = []

    for msg in messages:
        role = msg.get("role", "")
        if role == "system":
            system = msg["content"]
        elif role == "tool":
            converted.append({
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": msg.get("tool_call_id", ""),
                    "content": msg.get("content", ""),
                }],
            })
        elif role == "assistant" and msg.get("tool_calls"):
            content_blocks: list[dict] = []
            if msg.get("content"):
                content_blocks.append({"type": "text", "text": msg["content"]})
            for tc in msg["tool_calls"]:
                fn = tc.get("function", {})
                raw_args = fn.get("arguments", "{}")
                input_data = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                content_blocks.append({
                    "type": "tool_use",
                    "id": tc.get("id", ""),
                    "name": fn.get("name", ""),
                    "input": input_data,
                })
            converted.append({"role": "assistant", "content": content_blocks})
        else:
            converted.append(msg)

    return system, _merge_consecutive_roles(converted)


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
