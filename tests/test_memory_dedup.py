"""
Tests for memory deduplication on append in memory_ops.
"""

from __future__ import annotations

import pytest

from core.tools.memory_ops import make_update_memory_tool


@pytest.mark.asyncio
async def test_append_same_content_not_duplicated(tmp_path):
    tool = make_update_memory_tool("test_agent", tmp_path)

    await tool.handler({"target": "global_preferences", "content": "- prefer dark theme", "mode": "append"})
    await tool.handler({"target": "global_preferences", "content": "- prefer dark theme", "mode": "append"})

    prefs = (tmp_path / "global" / "preferences.md").read_text()
    assert prefs.count("prefer dark theme") == 1


@pytest.mark.asyncio
async def test_append_new_content_added(tmp_path):
    tool = make_update_memory_tool("test_agent", tmp_path)

    await tool.handler({"target": "global_preferences", "content": "- prefer dark theme", "mode": "append"})
    await tool.handler({"target": "global_preferences", "content": "- use vim keybindings", "mode": "append"})

    prefs = (tmp_path / "global" / "preferences.md").read_text()
    assert "prefer dark theme" in prefs
    assert "use vim keybindings" in prefs


@pytest.mark.asyncio
async def test_dedup_is_case_insensitive(tmp_path):
    tool = make_update_memory_tool("test_agent", tmp_path)

    await tool.handler({"target": "global_preferences", "content": "- Prefer Dark Theme", "mode": "append"})
    await tool.handler({"target": "global_preferences", "content": "- prefer dark theme", "mode": "append"})

    prefs = (tmp_path / "global" / "preferences.md").read_text()
    # Only one variant should be present
    count = prefs.lower().count("prefer dark theme")
    assert count == 1


@pytest.mark.asyncio
async def test_overwrite_ignores_dedup(tmp_path):
    """Overwrite mode replaces the entire file — dedup is not applied."""
    tool = make_update_memory_tool("test_agent", tmp_path)

    await tool.handler({"target": "global_preferences", "content": "- initial content", "mode": "append"})
    await tool.handler({"target": "global_preferences", "content": "- replaced content", "mode": "overwrite"})

    prefs = (tmp_path / "global" / "preferences.md").read_text()
    assert "initial content" not in prefs
    assert "replaced content" in prefs
