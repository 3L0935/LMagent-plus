"""
call_agent tool — delegate a task to a specialized sub-agent.

Bidirectional routing model:
  @assistant  →  coder | writer | research   (orchestrator, allowed_targets=["coder","writer","research"])
  coder|writer|research  →  assistant        (escalation only, allowed_targets=["assistant"])

allowed_targets controls both the JSON schema enum and the anti-loop guard.
Sub-agents receive global + per-agent memory hooks for full context continuity.
"""

from __future__ import annotations

from typing import Callable

from core.context_vars import persona_models_ctx, persona_setup_fn_ctx
from core.errors import ConfigError, ToolError
from core.persona_loader import load_persona, make_system_prompt_hook, resolve_tool_names
from core.tool_registry import ToolDefinition, ToolRegistry

if True:
    from typing import TYPE_CHECKING
    if TYPE_CHECKING:
        from core.agent import Agent
        from core.router import Router
        from core.memory import PARAStore


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
    store: "PARAStore | None" = None,
    app_hook: "Callable[[], str] | None" = None,
    allowed_targets: list[str] | None = None,
    caller_name: str | None = None,
) -> ToolDefinition:
    """
    Create and return a call_agent ToolDefinition.

    Args:
        router: LLM backend router used by sub-agents.
        base_registry: Full tool registry — sub-agents receive a filtered subset.
        store: PARAStore for injecting global + per-agent memory into sub-agents.
        app_hook: App-level system prompt hook (forwarded to sub-agents).
        allowed_targets: Constrains the 'name' enum in the JSON schema.
            @assistant gets ["coder", "writer", "research"].
            Specialists get ["assistant"].
        caller_name: Name of the calling persona — used to block self-delegation.
    """
    from core.agent import Agent

    targets = allowed_targets or ["coder", "writer", "research"]
    target_desc = ", ".join(targets)
    is_escalation = targets == ["assistant"]

    schema: dict = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": f"Target agent name: {target_desc}",
                "enum": targets,
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

    async def _handler(params: dict) -> dict:
        agent_name: str = params["name"]
        payload: dict = params["payload"]

        # Anti-loop guard: prevent self-delegation even if schema enum allows it.
        if caller_name and agent_name == caller_name:
            raise ToolError(
                f"Self-routing blocked: '{agent_name}' cannot delegate to itself via call_agent."
            )

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

        # Build system prompt hooks — forward app hook + global memory + per-agent memory
        hooks: list[Callable[[], str]] = []
        if app_hook:
            hooks.append(app_hook)
        if store:
            hooks.append(store.make_global_memory_hook())
        memory_fn = store.make_agent_memory_hook(agent_name) if store else None
        hooks.append(make_system_prompt_hook(persona, sub_registry, memory_fn=memory_fn))

        # Resolve model: per-persona override from CLI, else ask user (first call),
        # else fall back to persona's cloud_equivalent.
        persona_models = persona_models_ctx.get({})
        if agent_name in persona_models:
            sub_model: str | None = persona_models[agent_name]
        else:
            setup_fn = persona_setup_fn_ctx.get(None)
            if setup_fn is not None:
                chosen = await setup_fn(agent_name)
                sub_model = chosen or persona.get("cloud_equivalent")
                # Propagate the choice so subsequent calls in this request use it
                if chosen:
                    persona_models_ctx.set({**persona_models, agent_name: chosen})
            else:
                sub_model = persona.get("cloud_equivalent")

        # Run the sub-agent
        agent = Agent(
            router=router,
            tool_registry=sub_registry,
            system_prompt_hooks=hooks,
            cloud_equivalent=persona.get("cloud_equivalent"),
        )
        task_message = _format_task_message(payload)

        text_parts: list[str] = []
        tool_results: list[dict] = []
        errors: list[str] = []

        async for event in agent.run(task_message, model=sub_model):
            if event["type"] in ("text", "text_delta"):
                text_parts.append(event["content"])
            elif event["type"] == "tool_result":
                tool_results.append({"tool": event["name"], "output": event["output"]})
            elif event["type"] == "error":
                errors.append(event["message"])

        return {
            "agent": agent_name,
            "output": "".join(text_parts),
            "tool_results": tool_results,
            "errors": errors,
        }

    if is_escalation:
        description = "Escalate to @assistant when the task exceeds your available tools."
        when_to_use = "When the task exceeds available tools — escalate to @assistant only"
    else:
        description = (
            "Delegate a task to a specialized sub-agent (coder, writer, or research). "
            "The task payload must be a structured JSON object."
        )
        when_to_use = (
            "When the task clearly belongs to a specialized domain — "
            "prefer this over handling everything yourself"
        )

    return ToolDefinition(
        name="call_agent",
        description=description,
        input_schema=schema,
        handler=_handler,
        when_to_use=when_to_use,
    )
