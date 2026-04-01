"""
Git tools — clone, status, log. Delegates to bash_execute internally.
"""

from __future__ import annotations

from pathlib import Path

from core.errors import ToolError
from core.tool_registry import ToolDefinition
from core.tools.bash import bash_execute


async def git_clone(url: str, dest: str) -> dict:
    """Clone a git repository."""
    result = await bash_execute(f"git clone {url!r} {dest!r}", timeout=120)
    if result["returncode"] != 0:
        raise ToolError(f"git clone failed: {result['stderr']}")
    return {"success": True, "path": str(Path(dest).resolve())}


async def git_status(repo_path: str) -> dict:
    """Get git status of a repository."""
    result = await bash_execute("git status", cwd=Path(repo_path))
    if result["returncode"] != 0:
        raise ToolError(f"git status failed: {result['stderr']}")
    return {"output": result["stdout"]}


async def git_log(repo_path: str, n: int = 10) -> dict:
    """Get the last n commits of a repository."""
    result = await bash_execute(f"git log --oneline -n {n}", cwd=Path(repo_path))
    if result["returncode"] != 0:
        raise ToolError(f"git log failed: {result['stderr']}")
    return {"output": result["stdout"]}


GIT_CLONE_TOOL = ToolDefinition(
    name="git_clone",
    description="Clone a git repository to a local destination path.",
    input_schema={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Repository URL to clone"},
            "dest": {"type": "string", "description": "Local destination path"},
        },
        "required": ["url", "dest"],
        "additionalProperties": False,
    },
    handler=lambda p: git_clone(p["url"], p["dest"]),
    when_to_use="To clone a remote repository — only when the repo does not yet exist locally",
)

GIT_STATUS_TOOL = ToolDefinition(
    name="git_status",
    description="Get the git status of a local repository.",
    input_schema={
        "type": "object",
        "properties": {
            "repo_path": {"type": "string", "description": "Path to the git repository"},
        },
        "required": ["repo_path"],
        "additionalProperties": False,
    },
    handler=lambda p: git_status(p["repo_path"]),
    when_to_use="To check repository state before committing or after making changes",
)

GIT_LOG_TOOL = ToolDefinition(
    name="git_log",
    description="Get the commit log of a local git repository.",
    input_schema={
        "type": "object",
        "properties": {
            "repo_path": {"type": "string", "description": "Path to the git repository"},
            "n": {"type": "integer", "default": 10, "description": "Number of commits to show"},
        },
        "required": ["repo_path"],
        "additionalProperties": False,
    },
    handler=lambda p: git_log(p["repo_path"], p.get("n", 10)),
    when_to_use="To inspect recent commit history",
)
