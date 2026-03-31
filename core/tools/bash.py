"""
Bash tool — execute shell commands in a subprocess.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from core.errors import ToolError
from core.tool_registry import ToolDefinition


async def bash_execute(command: str, timeout: int = 30, cwd: Path | None = None) -> dict:
    """
    Execute a shell command.

    Args:
        command: Shell command string (executed via /bin/sh -c).
        timeout: Max seconds to wait. Raises ToolError on timeout.
        cwd: Working directory. Defaults to current directory.

    Returns:
        {"stdout": str, "stderr": str, "returncode": int}
    """
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(cwd) if cwd else None,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            raise ToolError(f"Command timed out after {timeout}s: {command!r}")
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError(f"Failed to execute command: {exc}") from exc

    return {
        "stdout": stdout_bytes.decode(errors="replace"),
        "stderr": stderr_bytes.decode(errors="replace"),
        "returncode": proc.returncode,
    }


async def _handler(params: dict) -> dict:
    cwd = Path(params["cwd"]) if params.get("cwd") else None
    return await bash_execute(
        command=params["command"],
        timeout=params.get("timeout", 30),
        cwd=cwd,
    )


BASH_TOOL = ToolDefinition(
    name="bash",
    description="Execute a shell command and return its stdout, stderr, and return code.",
    input_schema={
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Shell command to execute"},
            "timeout": {"type": "integer", "default": 30, "description": "Timeout in seconds"},
            "cwd": {"type": "string", "description": "Working directory (optional)"},
        },
        "required": ["command"],
        "additionalProperties": False,
    },
    handler=_handler,
)
