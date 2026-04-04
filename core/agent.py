"""
Agent loop with plugin pipeline for system prompt construction.

The agent calls the LLM, parses tool calls, executes them, and loops
until the LLM produces a text-only response or max_iterations is reached.

System prompt is assembled from a list of hook callables — each returns a string
fragment. Phases 3 (personas) and 4 (memory) register hooks here without modifying
core loop logic.
"""

from __future__ import annotations

import json
import logging
from typing import AsyncGenerator, Callable

from core.errors import ToolError
from core.tool_registry import ToolRegistry

if True:  # avoid circular at runtime
    from typing import TYPE_CHECKING
    if TYPE_CHECKING:
        from core.router import Router

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 10


class Agent:
    def __init__(
        self,
        router: "Router",
        tool_registry: ToolRegistry,
        system_prompt_hooks: list[Callable[[], str]] | None = None,
        max_iterations: int = MAX_ITERATIONS,
        cloud_equivalent: str | None = None,
    ) -> None:
        """
        Args:
            router: LLM backend router.
            tool_registry: Registered tools available to the agent.
            system_prompt_hooks: Callables that return system prompt fragments.
                Joined with double newlines. Register persona/memory hooks here.
            max_iterations: Hard limit on tool-call loops per request.
            cloud_equivalent: Persona-preferred cloud model (e.g. "claude-sonnet-4-6").
                Used as model default when no explicit override is provided via run().
        """
        self._router = router
        self._registry = tool_registry
        self._hooks: list[Callable[[], str]] = system_prompt_hooks or []
        self._max_iterations = max_iterations
        self._cloud_equivalent = cloud_equivalent

    def _build_system_prompt(self) -> str:
        fragments = [hook() for hook in self._hooks]
        tools = self._registry.list_tools()
        if tools:
            # Only append the fallback tool list if no hook has already injected one
            # via {tools_list} substitution (which includes when_to_use hints).
            joined = "\n\n".join(f for f in fragments if f)
            if not any(f"- {t.name}:" in joined for t in tools):
                tool_lines = "\n".join(f"- {t.name}: {t.description}" for t in tools)
                fragments.append(f"You have access to the following tools:\n{tool_lines}")
        return "\n\n".join(f for f in fragments if f)

    async def run(self, user_message: str, model: str | None = None) -> AsyncGenerator[dict, None]:
        """
        Execute the agent loop for a single user message.

        Yields events:
          {"type": "text_start"}                        — streaming text begins
          {"type": "text_delta",  "content": str}       — one token (streaming)
          {"type": "text_end"}                          — streaming text ends
          {"type": "text",        "content": str}       — full text (non-streaming fallback)
          {"type": "tool_call",   "name": str, "input": dict}
          {"type": "tool_result", "name": str, "output": dict}
          {"type": "error",       "message": str}
          {"type": "done"}
        """
        # Persona-preferred model takes effect when no explicit override is requested.
        effective_model = model or self._cloud_equivalent

        system_prompt = self._build_system_prompt()
        messages: list[dict] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_message})

        tools_api = self._registry.to_api_format()

        for iteration in range(self._max_iterations):
            # --- Attempt streaming first, fallback to non-streaming ---
            text_parts: list[str] = []
            tool_calls: list[dict] = []
            streaming_ok = False

            yield {"type": "text_start"}
            try:
                async for chunk in self._router.chat_completion_stream(
                    messages=messages,
                    tools=tools_api or None,
                    model=effective_model,
                ):
                    if chunk["type"] == "text_delta":
                        text_parts.append(chunk["content"])
                        yield {"type": "text_delta", "content": chunk["content"]}
                    elif chunk["type"] == "tool_calls":
                        tool_calls = chunk["tool_calls"]
                    elif chunk["type"] == "done":
                        streaming_ok = True
                        break
            except Exception:
                streaming_ok = False

            if not streaming_ok:
                # Fallback to non-streaming
                text_parts = []
                tool_calls = []
                try:
                    response = await self._router.chat_completion(
                        messages=messages,
                        tools=tools_api or None,
                        model=effective_model,
                    )
                    choice = response.get("choices", [{}])[0]
                    msg = choice.get("message", {})
                    text_content = msg.get("content") or ""
                    if text_content:
                        text_parts.append(text_content)
                        yield {"type": "text", "content": text_content}
                    tool_calls = msg.get("tool_calls") or []
                except Exception as exc:
                    yield {"type": "text_end"}
                    yield {"type": "error", "message": f"LLM call failed: {exc}"}
                    break

            text_content = "".join(text_parts)
            yield {"type": "text_end"}

            # Check for tool calls
            if not tool_calls:
                # No more tool calls — we're done
                break

            # Add assistant message to history
            messages.append({"role": "assistant", "content": text_content or None, "tool_calls": tool_calls})

            # Execute each tool call
            tool_results = []
            for tc in tool_calls:
                fn = tc.get("function", {})
                tool_name = fn.get("name", "")
                call_id = tc.get("id", "")

                # Parse arguments
                try:
                    raw_args = fn.get("arguments", "{}")
                    tool_input = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                except json.JSONDecodeError as exc:
                    yield {"type": "error", "message": f"Failed to parse tool args for '{tool_name}': {exc}"}
                    tool_input = {}

                yield {"type": "tool_call", "name": tool_name, "input": tool_input}

                # Validate + execute
                try:
                    self._registry.validate_input(tool_name, tool_input)
                    tool_def = self._registry.get(tool_name)
                    if tool_def is None:
                        raise ToolError(f"Unknown tool: '{tool_name}'")
                    output = await tool_def.handler(tool_input)
                except ToolError as exc:
                    output = {"error": str(exc)}
                    yield {"type": "error", "message": str(exc)}
                except Exception as exc:
                    output = {"error": f"Unexpected error: {exc}"}
                    yield {"type": "error", "message": f"Tool '{tool_name}' raised: {exc}"}

                yield {"type": "tool_result", "name": tool_name, "output": output}

                tool_results.append({
                    "tool_call_id": call_id,
                    "role": "tool",
                    "content": json.dumps(output),
                })

            messages.extend(tool_results)

        else:
            yield {"type": "error", "message": f"Max iterations ({self._max_iterations}) reached."}

        yield {"type": "done"}
