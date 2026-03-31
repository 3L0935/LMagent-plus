"""
Persona loader for LMAgent-Plus.

Loads and validates persona YAML files, resolves tool groups,
and provides hooks for agent.py's system_prompt_hooks pipeline.

Search order (first match wins):
  1. ~/.lmagent-plus/personas/   (user custom, private)
  2. {repo}/personas/            (bundled)
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import yaml

from core.errors import ConfigError
from core.tool_registry import ToolRegistry

REQUIRED_FIELDS = {"name", "description", "default_model", "system_prompt", "tools_enabled"}

# Group aliases → individual tool names
TOOL_GROUPS: dict[str, list[str]] = {
    "file_ops": ["read_file", "write_file", "list_directory"],
    "git": ["git_clone", "git_status", "git_log"],
}


def _personas_dirs() -> list[Path]:
    user_dir = Path.home() / ".lmagent-plus" / "personas"
    bundled_dir = Path(__file__).parent.parent / "personas"
    return [user_dir, bundled_dir]


def load_persona(name: str) -> dict:
    """Load a persona by name. Raises ConfigError if not found or invalid YAML."""
    for base in _personas_dirs():
        path = base / f"{name}.yaml"
        if path.exists():
            try:
                data = yaml.safe_load(path.read_text())
            except yaml.YAMLError as exc:
                raise ConfigError(f"Invalid YAML in persona '{name}': {exc}") from exc
            validate_persona(data)
            return data
    searched = [str(d) for d in _personas_dirs()]
    raise ConfigError(f"Persona '{name}' not found. Searched: {searched}")


def list_personas() -> list[str]:
    """List available persona names. User custom personas shadow bundled ones."""
    seen: set[str] = set()
    names: list[str] = []
    for base in _personas_dirs():
        if not base.exists():
            continue
        for path in sorted(base.glob("*.yaml")):
            if path.stem.startswith("_"):
                continue  # skip _base.yaml and other templates
            if path.stem not in seen:
                seen.add(path.stem)
                names.append(path.stem)
    return names


def validate_persona(data: dict) -> None:
    """Validate persona structure. Raises ConfigError on missing or invalid fields."""
    if not isinstance(data, dict):
        raise ConfigError("Persona must be a YAML mapping")
    missing = REQUIRED_FIELDS - data.keys()
    if missing:
        raise ConfigError(f"Persona missing required fields: {', '.join(sorted(missing))}")
    if not isinstance(data.get("tools_enabled"), list):
        raise ConfigError("'tools_enabled' must be a list")


def resolve_tool_names(tools_enabled: list[str]) -> list[str]:
    """Expand group aliases (file_ops, git) to individual tool names."""
    resolved: list[str] = []
    for entry in tools_enabled:
        resolved.extend(TOOL_GROUPS.get(entry, [entry]))
    return resolved


def get_tools_list_str(persona: dict, tool_registry: ToolRegistry) -> str:
    """
    Format the persona's enabled tools for {tools_list} substitution.

    Only lists tools that are both enabled in the persona AND registered
    in the registry — prevents listing unavailable tools in the prompt.
    """
    enabled = resolve_tool_names(persona.get("tools_enabled", []))
    lines: list[str] = []
    for name in enabled:
        tool = tool_registry.get(name)
        if tool is not None:
            lines.append(f"- {tool.name}: {tool.description}")
    return "\n".join(lines) if lines else "(no tools available)"


def make_system_prompt_hook(
    persona: dict,
    tool_registry: ToolRegistry,
    memory_fn: Callable[[], str] | None = None,
) -> Callable[[], str]:
    """
    Return a hook for agent.py's system_prompt_hooks list.

    The hook renders the persona's system_prompt with:
      {tools_list}    → filtered list of enabled + registered tools
      {memory_context} → output of memory_fn(), or empty string (Phase 4)

    Substitution is lazy (evaluated on each call) so tool registry changes
    are reflected without recreating the hook.
    """
    def hook() -> str:
        tools_str = get_tools_list_str(persona, tool_registry)
        memory_str = memory_fn() if memory_fn else ""
        prompt: str = persona["system_prompt"]
        return prompt.replace("{tools_list}", tools_str).replace("{memory_context}", memory_str)

    return hook
