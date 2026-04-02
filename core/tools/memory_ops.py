"""
Memory operation tools — read and update the agent memory files.

Paths are resolved internally so the model never needs to know the filesystem layout.
"""

from __future__ import annotations

import logging
from pathlib import Path

from core.errors import ToolError
from core.tool_registry import ToolDefinition

logger = logging.getLogger(__name__)

_VALID_TARGETS = {
    "global_preferences": ("global", "preferences.md"),
    "global_context":     ("global", "context.md"),
    "learned":            None,  # resolved with agent_name at call time
}


async def _update_memory(
    target: str,
    content: str,
    mode: str,
    agent_name: str,
    memory_base: Path,
) -> dict:
    if target not in _VALID_TARGETS:
        raise ToolError(
            f"Unknown memory target '{target}'. "
            f"Valid targets: {', '.join(_VALID_TARGETS)}"
        )
    if mode not in ("append", "overwrite"):
        raise ToolError(f"Invalid mode '{mode}'. Use 'append' or 'overwrite'.")

    if target == "learned":
        path = memory_base / "agents" / agent_name / "learned.md"
    else:
        subdir, filename = _VALID_TARGETS[target]  # type: ignore[misc]
        path = memory_base / subdir / filename

    path.parent.mkdir(parents=True, exist_ok=True)

    if mode == "overwrite":
        path.write_text(content, encoding="utf-8")
    else:
        # Deduplicate: skip lines already present (case-insensitive, stripped)
        existing_lines: set[str] = set()
        if path.exists():
            existing_lines = {
                line.strip().lower()
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            }

        new_lines = [line for line in content.splitlines() if line.strip()]
        unique_lines = [line for line in new_lines if line.strip().lower() not in existing_lines]
        skipped = len(new_lines) - len(unique_lines)
        if skipped:
            logger.warning("update_memory: skipped %d duplicate line(s) for %s", skipped, target)

        if unique_lines:
            with path.open("a", encoding="utf-8") as f:
                f.write("\n")
                f.write("\n".join(unique_lines))

    return {"success": True, "path": str(path), "target": target, "mode": mode}


def make_update_memory_tool(agent_name: str, memory_base: Path) -> ToolDefinition:
    """
    Build the update_memory ToolDefinition bound to a specific agent and memory base dir.

    Args:
        agent_name: Name of the current agent (used to resolve `learned` path).
        memory_base: Path to ~/.lmagent-plus/memory/.
    """
    async def _handler(params: dict) -> dict:
        return await _update_memory(
            target=params["target"],
            content=params["content"],
            mode=params.get("mode", "append"),
            agent_name=agent_name,
            memory_base=memory_base,
        )

    return ToolDefinition(
        name="update_memory",
        description=(
            "Persist information across sessions by writing to the agent memory files. "
            "Use this whenever the user states a preference, you observe a recurring pattern, "
            "or important context should be remembered for future conversations."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "enum": ["global_preferences", "global_context", "learned"],
                    "description": (
                        "Which memory file to update:\n"
                        "- global_preferences: user preferences visible to all agents "
                        "(language, tone, shell, editor, workflow habits)\n"
                        "- global_context: shared state visible to all agents "
                        "(active projects, important facts, recent decisions)\n"
                        "- learned: patterns specific to this agent "
                        "(observed preferences, mistakes to avoid)"
                    ),
                },
                "content": {
                    "type": "string",
                    "description": "Markdown content to write. Use concise bullet points.",
                },
                "mode": {
                    "type": "string",
                    "enum": ["append", "overwrite"],
                    "description": "append (default) adds content at the end. overwrite replaces the entire file.",
                },
            },
            "required": ["target", "content"],
            "additionalProperties": False,
        },
        handler=_handler,
        when_to_use=(
            "When the user expresses a preference, asks you to remember something, "
            "or when you detect a recurring pattern worth persisting."
        ),
    )
