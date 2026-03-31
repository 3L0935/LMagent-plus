"""
Tests for core.persona_loader.

Covers: load_persona, list_personas, validate_persona, resolve_tool_names,
        get_tools_list_str, make_system_prompt_hook.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml

from core.errors import ConfigError
from core.persona_loader import (
    get_tools_list_str,
    list_personas,
    load_persona,
    make_system_prompt_hook,
    resolve_tool_names,
    validate_persona,
    TOOL_GROUPS,
)
from core.tool_registry import ToolDefinition, ToolRegistry


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MINIMAL_PERSONA = {
    "name": "test",
    "description": "A test persona",
    "default_model": "mistral-7b-q4",
    "system_prompt": "You are a test agent.\n\nTools:\n{tools_list}\n\n{memory_context}",
    "tools_enabled": ["bash"],
}


@pytest.fixture
def persona_dir(tmp_path: Path) -> Path:
    d = tmp_path / "personas"
    d.mkdir()
    return d


@pytest.fixture
def minimal_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(ToolDefinition(
        name="bash",
        description="Execute a shell command.",
        input_schema={"type": "object", "properties": {"cmd": {"type": "string"}}, "required": ["cmd"]},
        handler=AsyncMock(return_value={"stdout": "ok"}),
    ))
    registry.register(ToolDefinition(
        name="read_file",
        description="Read a file.",
        input_schema={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
        handler=AsyncMock(return_value={"content": ""}),
    ))
    registry.register(ToolDefinition(
        name="write_file",
        description="Write a file.",
        input_schema={"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]},
        handler=AsyncMock(return_value={}),
    ))
    registry.register(ToolDefinition(
        name="list_directory",
        description="List a directory.",
        input_schema={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
        handler=AsyncMock(return_value={"entries": []}),
    ))
    return registry


# ---------------------------------------------------------------------------
# validate_persona
# ---------------------------------------------------------------------------

def test_validate_persona_valid():
    validate_persona(MINIMAL_PERSONA)  # no exception


def test_validate_persona_not_dict():
    with pytest.raises(ConfigError, match="YAML mapping"):
        validate_persona("not a dict")


def test_validate_persona_missing_fields():
    incomplete = {"name": "x", "description": "y"}
    with pytest.raises(ConfigError, match="missing required fields"):
        validate_persona(incomplete)


def test_validate_persona_tools_enabled_not_list():
    bad = {**MINIMAL_PERSONA, "tools_enabled": "bash"}
    with pytest.raises(ConfigError, match="must be a list"):
        validate_persona(bad)


# ---------------------------------------------------------------------------
# load_persona
# ---------------------------------------------------------------------------

def test_load_persona_found(persona_dir: Path, monkeypatch):
    path = persona_dir / "myagent.yaml"
    path.write_text(yaml.dump(MINIMAL_PERSONA))

    monkeypatch.setattr(
        "core.persona_loader._personas_dirs",
        lambda: [persona_dir],
    )
    data = load_persona("myagent")
    assert data["name"] == "test"


def test_load_persona_not_found(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("core.persona_loader._personas_dirs", lambda: [tmp_path / "nonexistent"])
    with pytest.raises(ConfigError, match="not found"):
        load_persona("ghost")


def test_load_persona_invalid_yaml(persona_dir: Path, monkeypatch):
    path = persona_dir / "broken.yaml"
    path.write_text("name: [\nbad yaml")
    monkeypatch.setattr("core.persona_loader._personas_dirs", lambda: [persona_dir])
    with pytest.raises(ConfigError, match="Invalid YAML"):
        load_persona("broken")


def test_load_persona_missing_required_field(persona_dir: Path, monkeypatch):
    bad = {k: v for k, v in MINIMAL_PERSONA.items() if k != "description"}
    path = persona_dir / "bad.yaml"
    path.write_text(yaml.dump(bad))
    monkeypatch.setattr("core.persona_loader._personas_dirs", lambda: [persona_dir])
    with pytest.raises(ConfigError, match="missing required fields"):
        load_persona("bad")


# ---------------------------------------------------------------------------
# list_personas
# ---------------------------------------------------------------------------

def test_list_personas_skips_templates(persona_dir: Path, monkeypatch):
    (persona_dir / "_base.yaml").write_text(yaml.dump(MINIMAL_PERSONA))
    (persona_dir / "coder.yaml").write_text(yaml.dump({**MINIMAL_PERSONA, "name": "coder"}))
    monkeypatch.setattr("core.persona_loader._personas_dirs", lambda: [persona_dir])
    names = list_personas()
    assert "coder" in names
    assert "_base" not in names


def test_list_personas_deduplicates(persona_dir: Path, tmp_path: Path, monkeypatch):
    dir2 = tmp_path / "bundled"
    dir2.mkdir()
    for d in (persona_dir, dir2):
        (d / "coder.yaml").write_text(yaml.dump({**MINIMAL_PERSONA, "name": "coder"}))
    monkeypatch.setattr("core.persona_loader._personas_dirs", lambda: [persona_dir, dir2])
    names = list_personas()
    assert names.count("coder") == 1


def test_list_personas_empty_dirs(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("core.persona_loader._personas_dirs", lambda: [tmp_path / "nope"])
    assert list_personas() == []


# ---------------------------------------------------------------------------
# resolve_tool_names
# ---------------------------------------------------------------------------

def test_resolve_tool_names_direct():
    assert resolve_tool_names(["bash"]) == ["bash"]


def test_resolve_tool_names_group_file_ops():
    assert resolve_tool_names(["file_ops"]) == TOOL_GROUPS["file_ops"]


def test_resolve_tool_names_group_git():
    assert resolve_tool_names(["git"]) == TOOL_GROUPS["git"]


def test_resolve_tool_names_mixed():
    result = resolve_tool_names(["bash", "file_ops"])
    assert result == ["bash"] + TOOL_GROUPS["file_ops"]


# ---------------------------------------------------------------------------
# get_tools_list_str
# ---------------------------------------------------------------------------

def test_get_tools_list_str_single(minimal_registry: ToolRegistry):
    persona = {**MINIMAL_PERSONA, "tools_enabled": ["bash"]}
    result = get_tools_list_str(persona, minimal_registry)
    assert "bash" in result
    assert "Execute a shell command." in result
    assert "read_file" not in result


def test_get_tools_list_str_group(minimal_registry: ToolRegistry):
    persona = {**MINIMAL_PERSONA, "tools_enabled": ["file_ops"]}
    result = get_tools_list_str(persona, minimal_registry)
    assert "read_file" in result
    assert "write_file" in result
    assert "list_directory" in result
    assert "bash" not in result


def test_get_tools_list_str_skips_unregistered(minimal_registry: ToolRegistry):
    persona = {**MINIMAL_PERSONA, "tools_enabled": ["bash", "web_search"]}
    result = get_tools_list_str(persona, minimal_registry)
    assert "bash" in result
    assert "web_search" not in result


def test_get_tools_list_str_empty_registry():
    persona = {**MINIMAL_PERSONA, "tools_enabled": ["bash"]}
    result = get_tools_list_str(persona, ToolRegistry())
    assert result == "(no tools available)"


# ---------------------------------------------------------------------------
# make_system_prompt_hook
# ---------------------------------------------------------------------------

def test_make_system_prompt_hook_substitutes_tools(minimal_registry: ToolRegistry):
    hook = make_system_prompt_hook(MINIMAL_PERSONA, minimal_registry)
    result = hook()
    assert "{tools_list}" not in result
    assert "bash" in result


def test_make_system_prompt_hook_substitutes_memory(minimal_registry: ToolRegistry):
    memory_fn = lambda: "## Projects\n- LMAgent-Plus"
    hook = make_system_prompt_hook(MINIMAL_PERSONA, minimal_registry, memory_fn=memory_fn)
    result = hook()
    assert "{memory_context}" not in result
    assert "LMAgent-Plus" in result


def test_make_system_prompt_hook_no_memory_fn(minimal_registry: ToolRegistry):
    hook = make_system_prompt_hook(MINIMAL_PERSONA, minimal_registry)
    result = hook()
    assert "{memory_context}" not in result


def test_make_system_prompt_hook_lazy_evaluation(minimal_registry: ToolRegistry):
    """Hook re-evaluates the tool list on each call."""
    persona = {**MINIMAL_PERSONA, "tools_enabled": ["bash"]}
    hook = make_system_prompt_hook(persona, minimal_registry)

    result1 = hook()
    assert "bash" in result1

    # Register a new tool after hook creation — should appear on next call
    minimal_registry.register(ToolDefinition(
        name="git_status",
        description="Get git status.",
        input_schema={"type": "object", "properties": {}, "required": []},
        handler=AsyncMock(return_value={}),
    ))
    persona["tools_enabled"] = ["bash", "git_status"]
    result2 = hook()
    assert "git_status" in result2


# ---------------------------------------------------------------------------
# Integration: bundled personas are valid
# ---------------------------------------------------------------------------

def test_bundled_personas_are_valid():
    """All bundled persona YAMLs (non-template) pass validation."""
    bundled_dir = Path(__file__).parent.parent / "personas"
    for path in bundled_dir.glob("*.yaml"):
        if path.stem.startswith("_"):
            continue
        data = yaml.safe_load(path.read_text())
        validate_persona(data)  # must not raise
