"""
call_agent tool — delegate a task to a specialized sub-agent.

This tool is intended for the @assistant orchestrator persona.
It loads the target persona, builds a scoped tool registry and system prompt,
runs the sub-agent loop, and returns the aggregated result.

Usage by the LLM:
    call_agent(name="coder", payload={"task": "fix bug in auth.py", "files": ["auth.py"]})

Design notes:
- The handler is not a module-level constant — it captures router and base_registry
  at setup time via make_call_agent_tool().
- The target agent name is constrained to known personas (no self-routing to "assistant").
- Payload is a structured JSON object, not free text, to avoid context loss across hops.
"""

from __future__ import annotations

import json

from core.errors import ConfigError, ToolError
from core.persona_loader import load_persona, make_system_prompt_hook, resolve_tool_names
from core.tool_registry import ToolDefinition, ToolRegistry

if True:
    from typing import TYPE_CHECKING
    if TYPE_CHECKING:
        from core.agent import Agent
        from core.router import Router


# JSON Schema for the call_agent input
CALL_AGENT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "name": {
            "type": "string",
            "description": "Target agent name: coder, writer, or research",
            "enum": ["coder", "writer", "research"],
        },
        "payload": {
            "type": "object",
            "description": "Structured task payload passed to the target agent",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "The task to perform — be specific and actionable",
                },
                "files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Relevant file paths the agent should focus on",
                },
                "constraints": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Constraints the agent must respect (e.g. 'do not change the API')",
                },
                "context": {
                    "type": "string",
                    "description": "Additional context to help the agent understand the task",
                },
            },
            "required": ["task"],
            "additionalProperties": False,
        },
    },
    "required": ["name", "payload"],
    "additionalProperties": False,
}


def _format_task_message(payload: dict) -> str:
    """Convert a structured payload dict to a user message string for the sub-agent."""
    parts = [payload["task"]]
    if payload.get("files"):
        parts.append("Files: " + ", ".join(payload["files"]))
    if payload.get("constraints"):
        constraint_lines = "\n".join(f"- {c}" for c in payload["constraints"])
        parts.append(f"Constraints:\n{constraint_lines}")
    if payload.get("context"):
        parts.append(f"Context: {payload['context']}")
    return "\n\n".join(parts)


def make_call_agent_tool(
    router: "Router",
    base_registry: ToolRegistry,
) -> ToolDefinition:
    """
    Create and return a call_agent ToolDefinition bound to the given router and registry.

    Args:
        router: The LLM backend router used by sub-agents.
        base_registry: The full tool registry — sub-agents receive a filtered subset.

    Returns:
        A ToolDefinition for 'call_agent' ready to register.
    """
    # Import here to avoid circular imports at module level
    from core.agent import Agent

    async def _handler(params: dict) -> dict:
        agent_name: str = params["name"]
        payload: dict = params["payload"]

        # Load target persona
        try:
            persona = load_persona(agent_name)
        except ConfigError as exc:
            raise ToolError(f"Unknown agent '{agent_name}': {exc}") from exc

        # Build a scoped registry with only the persona's enabled tools
        sub_registry = ToolRegistry()
        for tool_name in resolve_tool_names(persona.get("tools_enabled", [])):
            tool = base_registry.get(tool_name)
            if tool is not None:
                sub_registry.register(tool)

        # Build the persona system prompt hook
        hook = make_system_prompt_hook(persona, sub_registry)

        # Run the sub-agent
        agent = Agent(
            router=router,
            tool_registry=sub_registry,
            system_prompt_hooks=[hook],
        )
        task_message = _format_task_message(payload)

        text_parts: list[str] = []
        tool_results: list[dict] = []
        errors: list[str] = []

        async for event in agent.run(task_message):
            if event["type"] == "text":
                text_parts.append(event["content"])
            elif event["type"] == "tool_result":
                tool_results.append({"tool": event["name"], "output": event["output"]})
            elif event["type"] == "error":
                errors.append(event["message"])

        return {
            "agent": agent_name,
            "output": "\n".join(text_parts),
            "tool_results": tool_results,
            "errors": errors,
        }

    return ToolDefinition(
        name="call_agent",
        description=(
            "Delegate a task to a specialized sub-agent (coder, writer, or research). "
            "The task payload must be a structured JSON object."
        ),
        input_schema=CALL_AGENT_SCHEMA,
        handler=_handler,
        when_to_use=(
            "When the task clearly belongs to a specialized domain — "
            "prefer this over handling everything yourself"
        ),
    )
