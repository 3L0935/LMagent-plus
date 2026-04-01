"""
PARAStore — filesystem PARA memory management.

Manages `~/.lmagent-plus/memory/` and `~/.lmagent-plus/sessions/`.
Injectable paths for testability.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Callable

from core.config import MemoryConfig, USER_DIR

MEMORY_DIR = USER_DIR / "memory"
SESSIONS_DIR = USER_DIR / "sessions"

_CHARS_PER_TOKEN = 4


class PARAStore:
    def __init__(
        self,
        config: MemoryConfig,
        base_dir: Path = MEMORY_DIR,
        sessions_dir: Path = SESSIONS_DIR,
    ) -> None:
        self._config = config
        self._base = base_dir
        self._sessions = sessions_dir

    # ------------------------------------------------------------------
    # Structure

    def ensure_structure(self, agent_name: str) -> None:
        """Create directory structure and default files if missing."""
        global_dir = self._base / "global"
        agent_dir = self._base / "agents" / agent_name
        self._sessions.mkdir(parents=True, exist_ok=True)
        global_dir.mkdir(parents=True, exist_ok=True)
        agent_dir.mkdir(parents=True, exist_ok=True)

        _init_file(
            global_dir / "context.md",
            "# Global context\n\n## Active projects\n\n## Important facts\n\n"
            "## User preferences\n\n## Recent decisions\n",
        )
        _init_file(
            global_dir / "preferences.md",
            "# User preferences\n\n## Communication\n\n## Technical\n\n## Workflow\n",
        )
        _init_file(
            agent_dir / "recent_tasks.md",
            f"# Recent tasks — {agent_name.capitalize()}\n",
        )
        _init_file(
            agent_dir / "learned.md",
            f"# Learned patterns — {agent_name.capitalize()}\n\n"
            "## Observed preferences\n\n## Mistakes to avoid\n",
        )

    # ------------------------------------------------------------------
    # Reads

    def read_global(self) -> str:
        """Read global/context.md + global/preferences.md, truncated to max_global_tokens."""
        parts = []
        for name in ("context.md", "preferences.md"):
            path = self._base / "global" / name
            if path.exists():
                parts.append(path.read_text(encoding="utf-8"))
        return self._truncate("\n\n".join(parts), self._config.max_global_tokens)

    def read_agent(self, agent_name: str) -> str:
        """Read agents/<name>/recent_tasks.md + learned.md, truncated to max_agent_tokens."""
        agent_dir = self._base / "agents" / agent_name
        parts = []
        for name in ("recent_tasks.md", "learned.md"):
            path = agent_dir / name
            if path.exists():
                parts.append(path.read_text(encoding="utf-8"))
        return self._truncate("\n\n".join(parts), self._config.max_agent_tokens)

    # ------------------------------------------------------------------
    # Writes

    def archive_session(self, agent_name: str, summary: str) -> Path:
        """
        Save summary to ~/.lmagent-plus/sessions/YYYY-MM-DD-<agent>-NN.md.

        Returns the path of the created file.
        """
        self._sessions.mkdir(parents=True, exist_ok=True)
        today = date.today().isoformat()
        prefix = f"{today}-{agent_name}-"
        existing = sorted(self._sessions.glob(f"{prefix}*.md"))
        nn = len(existing) + 1
        path = self._sessions / f"{prefix}{nn:02d}.md"
        path.write_text(summary, encoding="utf-8")
        return path

    def append_recent_task(
        self, agent_name: str, task_date: str, tasks: list[str]
    ) -> None:
        """Append a dated entry to agents/<name>/recent_tasks.md."""
        agent_dir = self._base / "agents" / agent_name
        agent_dir.mkdir(parents=True, exist_ok=True)
        path = agent_dir / "recent_tasks.md"
        lines = [f"\n## {task_date}"]
        for task in tasks:
            lines.append(f"- {task}")
        with path.open("a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

    # ------------------------------------------------------------------
    # Plugin pipeline hooks (for core/agent.py)

    def make_global_memory_hook(self) -> Callable[[], str]:
        """Return a zero-argument callable that injects global memory into the system prompt."""
        def _hook() -> str:
            content = self.read_global()
            if not content.strip():
                return ""
            return f"## Global memory\n\n{content}"
        return _hook

    def make_agent_memory_hook(self, agent_name: str) -> Callable[[], str]:
        """Return a zero-argument callable that injects per-agent memory into the system prompt."""
        def _hook() -> str:
            content = self.read_agent(agent_name)
            if not content.strip():
                return ""
            return f"## Agent memory ({agent_name})\n\n{content}"
        return _hook

    # ------------------------------------------------------------------
    # Internal

    @staticmethod
    def _truncate(text: str, max_tokens: int) -> str:
        """Truncate text to at most max_tokens (4 chars/token heuristic).

        Cuts at the last newline to avoid broken lines.
        """
        max_chars = max_tokens * _CHARS_PER_TOKEN
        if len(text) <= max_chars:
            return text
        cut = text[:max_chars]
        last_nl = cut.rfind("\n")
        return cut[:last_nl] if last_nl != -1 else cut


def _init_file(path: Path, default_content: str) -> None:
    """Write default_content to path only if it does not already exist."""
    if not path.exists():
        path.write_text(default_content, encoding="utf-8")
