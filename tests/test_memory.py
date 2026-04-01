"""Tests for core.memory.PARAStore."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.config import MemoryConfig
from core.memory import PARAStore


@pytest.fixture
def config() -> MemoryConfig:
    return MemoryConfig(max_global_tokens=100, max_agent_tokens=50)


@pytest.fixture
def store(tmp_path: Path, config: MemoryConfig) -> PARAStore:
    return PARAStore(
        config=config,
        base_dir=tmp_path / "memory",
        sessions_dir=tmp_path / "sessions",
    )


# ---------------------------------------------------------------------------
# ensure_structure
# ---------------------------------------------------------------------------

class TestEnsureStructure:
    def test_creates_global_dirs(self, store, tmp_path):
        store.ensure_structure("coder")
        assert (tmp_path / "memory" / "global").is_dir()

    def test_creates_agent_dir(self, store, tmp_path):
        store.ensure_structure("coder")
        assert (tmp_path / "memory" / "agents" / "coder").is_dir()

    def test_creates_sessions_dir(self, store, tmp_path):
        store.ensure_structure("coder")
        assert (tmp_path / "sessions").is_dir()

    def test_creates_global_context_file(self, store, tmp_path):
        store.ensure_structure("coder")
        f = tmp_path / "memory" / "global" / "context.md"
        assert f.exists()
        assert "Global context" in f.read_text()

    def test_creates_global_preferences_file(self, store, tmp_path):
        store.ensure_structure("coder")
        f = tmp_path / "memory" / "global" / "preferences.md"
        assert f.exists()
        assert "User preferences" in f.read_text()

    def test_creates_agent_recent_tasks(self, store, tmp_path):
        store.ensure_structure("writer")
        f = tmp_path / "memory" / "agents" / "writer" / "recent_tasks.md"
        assert f.exists()
        assert "Writer" in f.read_text()

    def test_creates_agent_learned(self, store, tmp_path):
        store.ensure_structure("writer")
        f = tmp_path / "memory" / "agents" / "writer" / "learned.md"
        assert f.exists()
        assert "Learned patterns" in f.read_text()

    def test_does_not_overwrite_existing_files(self, store, tmp_path):
        store.ensure_structure("coder")
        context_path = tmp_path / "memory" / "global" / "context.md"
        context_path.write_text("custom content", encoding="utf-8")
        store.ensure_structure("coder")
        assert context_path.read_text() == "custom content"


# ---------------------------------------------------------------------------
# read_global
# ---------------------------------------------------------------------------

class TestReadGlobal:
    def test_returns_empty_string_when_no_files(self, store):
        result = store.read_global()
        assert result == ""

    def test_reads_context_file(self, store, tmp_path):
        global_dir = tmp_path / "memory" / "global"
        global_dir.mkdir(parents=True)
        (global_dir / "context.md").write_text("hello context", encoding="utf-8")
        assert "hello context" in store.read_global()

    def test_reads_both_files(self, store, tmp_path):
        global_dir = tmp_path / "memory" / "global"
        global_dir.mkdir(parents=True)
        (global_dir / "context.md").write_text("context content", encoding="utf-8")
        (global_dir / "preferences.md").write_text("prefs content", encoding="utf-8")
        result = store.read_global()
        assert "context content" in result
        assert "prefs content" in result

    def test_truncates_to_max_global_tokens(self, store, tmp_path):
        global_dir = tmp_path / "memory" / "global"
        global_dir.mkdir(parents=True)
        # 100 tokens * 4 chars = 400 chars max; write 1000 chars
        (global_dir / "context.md").write_text("x" * 1000, encoding="utf-8")
        result = store.read_global()
        assert len(result) <= 100 * 4


# ---------------------------------------------------------------------------
# read_agent
# ---------------------------------------------------------------------------

class TestReadAgent:
    def test_returns_empty_string_when_no_files(self, store):
        result = store.read_agent("coder")
        assert result == ""

    def test_reads_recent_tasks(self, store, tmp_path):
        agent_dir = tmp_path / "memory" / "agents" / "coder"
        agent_dir.mkdir(parents=True)
        (agent_dir / "recent_tasks.md").write_text("## 2026-03-31\n- did stuff", encoding="utf-8")
        assert "did stuff" in store.read_agent("coder")

    def test_reads_both_files(self, store, tmp_path):
        agent_dir = tmp_path / "memory" / "agents" / "coder"
        agent_dir.mkdir(parents=True)
        (agent_dir / "recent_tasks.md").write_text("recent", encoding="utf-8")
        (agent_dir / "learned.md").write_text("learned", encoding="utf-8")
        result = store.read_agent("coder")
        assert "recent" in result
        assert "learned" in result

    def test_truncates_to_max_agent_tokens(self, store, tmp_path):
        agent_dir = tmp_path / "memory" / "agents" / "coder"
        agent_dir.mkdir(parents=True)
        # 50 tokens * 4 chars = 200 chars max; write 500 chars
        (agent_dir / "recent_tasks.md").write_text("y" * 500, encoding="utf-8")
        result = store.read_agent("coder")
        assert len(result) <= 50 * 4


# ---------------------------------------------------------------------------
# archive_session
# ---------------------------------------------------------------------------

class TestArchiveSession:
    def test_creates_session_file(self, store, tmp_path):
        path = store.archive_session("coder", "summary text")
        assert path.exists()
        assert path.read_text() == "summary text"

    def test_filename_contains_agent_and_date(self, store):
        from datetime import date
        today = date.today().isoformat()
        path = store.archive_session("writer", "content")
        assert today in path.name
        assert "writer" in path.name

    def test_increments_nn_on_multiple_calls(self, store):
        p1 = store.archive_session("coder", "first")
        p2 = store.archive_session("coder", "second")
        assert p1 != p2
        assert p1.stem.endswith("-01")
        assert p2.stem.endswith("-02")

    def test_creates_sessions_dir_if_missing(self, tmp_path, config):
        sessions = tmp_path / "new_sessions"
        store = PARAStore(config, tmp_path / "memory", sessions)
        path = store.archive_session("coder", "x")
        assert sessions.is_dir()
        assert path.exists()


# ---------------------------------------------------------------------------
# append_recent_task
# ---------------------------------------------------------------------------

class TestAppendRecentTask:
    def test_creates_file_if_missing(self, store, tmp_path):
        store.append_recent_task("coder", "2026-04-01", ["task A"])
        path = tmp_path / "memory" / "agents" / "coder" / "recent_tasks.md"
        assert path.exists()

    def test_appends_date_header(self, store, tmp_path):
        store.append_recent_task("coder", "2026-04-01", ["task A"])
        content = (tmp_path / "memory" / "agents" / "coder" / "recent_tasks.md").read_text()
        assert "## 2026-04-01" in content

    def test_appends_tasks_as_list_items(self, store, tmp_path):
        store.append_recent_task("coder", "2026-04-01", ["task A", "task B"])
        content = (tmp_path / "memory" / "agents" / "coder" / "recent_tasks.md").read_text()
        assert "- task A" in content
        assert "- task B" in content

    def test_multiple_calls_accumulate(self, store, tmp_path):
        store.append_recent_task("coder", "2026-04-01", ["task A"])
        store.append_recent_task("coder", "2026-04-02", ["task B"])
        content = (tmp_path / "memory" / "agents" / "coder" / "recent_tasks.md").read_text()
        assert "## 2026-04-01" in content
        assert "## 2026-04-02" in content

    def test_appends_to_existing_file(self, store, tmp_path):
        agent_dir = tmp_path / "memory" / "agents" / "coder"
        agent_dir.mkdir(parents=True)
        (agent_dir / "recent_tasks.md").write_text("# Recent tasks\n", encoding="utf-8")
        store.append_recent_task("coder", "2026-04-01", ["new task"])
        content = (agent_dir / "recent_tasks.md").read_text()
        assert "# Recent tasks" in content
        assert "new task" in content


# ---------------------------------------------------------------------------
# Plugin pipeline hooks
# ---------------------------------------------------------------------------

class TestMakeGlobalMemoryHook:
    def test_returns_callable(self, store):
        hook = store.make_global_memory_hook()
        assert callable(hook)

    def test_returns_empty_string_when_no_memory(self, store):
        hook = store.make_global_memory_hook()
        assert hook() == ""

    def test_injects_global_memory_header(self, store, tmp_path):
        global_dir = tmp_path / "memory" / "global"
        global_dir.mkdir(parents=True)
        (global_dir / "context.md").write_text("important fact", encoding="utf-8")
        hook = store.make_global_memory_hook()
        result = hook()
        assert "## Global memory" in result
        assert "important fact" in result


class TestMakeAgentMemoryHook:
    def test_returns_callable(self, store):
        hook = store.make_agent_memory_hook("coder")
        assert callable(hook)

    def test_returns_empty_string_when_no_memory(self, store):
        hook = store.make_agent_memory_hook("coder")
        assert hook() == ""

    def test_injects_agent_memory_header(self, store, tmp_path):
        agent_dir = tmp_path / "memory" / "agents" / "coder"
        agent_dir.mkdir(parents=True)
        (agent_dir / "recent_tasks.md").write_text("did a task", encoding="utf-8")
        hook = store.make_agent_memory_hook("coder")
        result = hook()
        assert "## Agent memory (coder)" in result
        assert "did a task" in result


# ---------------------------------------------------------------------------
# _truncate
# ---------------------------------------------------------------------------

class TestTruncate:
    def test_short_text_unchanged(self):
        assert PARAStore._truncate("hello", 100) == "hello"

    def test_long_text_truncated(self):
        text = "a" * 1000
        result = PARAStore._truncate(text, 10)
        assert len(result) <= 10 * 4

    def test_cuts_at_newline(self):
        # 10 tokens * 4 = 40 chars; put a newline at char 30
        text = "a" * 29 + "\n" + "b" * 500
        result = PARAStore._truncate(text, 10)
        assert result.endswith("\n") or not result.endswith("b")
        assert "b" not in result

    def test_cuts_at_char_boundary_if_no_newline(self):
        text = "a" * 1000
        result = PARAStore._truncate(text, 10)
        assert len(result) == 40
