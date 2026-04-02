"""
Git tools — clone, status, log. Uses asyncio.create_subprocess_exec to avoid shell injection.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from core.errors import ToolError
from core.tool_registry import ToolDefinition

# Shell metacharacters that have no place in a legitimate git URL
_URL_BLOCKED_CHARS = frozenset({";", "|", "&", "`", "$"})


def _validate_git_url(url: str) -> None:
    """Raise ToolError if the URL contains shell metacharacters."""
    for ch in _URL_BLOCKED_CHARS:
        if ch in url:
            raise ToolError(
                f"Invalid git URL: contains forbidden character {ch!r}"
            )


async def git_clone(url: str, dest: str) -> dict:
    """Clone a git repository using subprocess exec (no shell injection)."""
    _validate_git_url(url)
    proc = await asyncio.create_subprocess_exec(
        "git", "clone", "--", url, dest,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
    if proc.returncode != 0:
        raise ToolError(f"git clone failed: {stderr.decode(errors='replace')}")
    return {"success": True, "path": str(Path(dest).resolve())}


async def git_status(repo_path: str) -> dict:
    """Get git status of a repository."""
    proc = await asyncio.create_subprocess_exec(
        "git", "status",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=repo_path,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
    if proc.returncode != 0:
        raise ToolError(f"git status failed: {stderr.decode(errors='replace')}")
    return {"output": stdout.decode(errors="replace")}


async def git_log(repo_path: str, n: int = 10) -> dict:
    """Get the last n commits of a repository."""
    proc = await asyncio.create_subprocess_exec(
        "git", "log", "--oneline", f"-n{n}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=repo_path,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
    if proc.returncode != 0:
        raise ToolError(f"git log failed: {stderr.decode(errors='replace')}")
    return {"output": stdout.decode(errors="replace")}


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
