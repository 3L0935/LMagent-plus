"""
File operation tools — read, write, list directory.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from core.errors import ToolError
from core.tool_registry import ToolDefinition
from core.tools._path_guard import check_path

if TYPE_CHECKING:
    from core.config import SecurityConfig


async def read_file(path: str, security: "SecurityConfig") -> dict:
    """Read a file and return its content."""
    check_path(path, security)
    p = Path(path)
    try:
        content = p.read_text(errors="replace")
    except FileNotFoundError:
        raise ToolError(f"File not found: {path}")
    except OSError as exc:
        raise ToolError(f"Cannot read file {path}: {exc}") from exc
    return {"content": content, "path": str(p.resolve())}


async def write_file(path: str, content: str, security: "SecurityConfig") -> dict:
    """Write content to a file (creates parent dirs as needed)."""
    check_path(path, security)
    p = Path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    except OSError as exc:
        raise ToolError(f"Cannot write file {path}: {exc}") from exc
    return {"success": True, "path": str(p.resolve())}


async def list_directory(path: str, security: "SecurityConfig") -> dict:
    """List directory entries with name, type, and size."""
    check_path(path, security)
    p = Path(path)
    try:
        entries = []
        for entry in sorted(p.iterdir(), key=lambda e: (e.is_file(), e.name)):
            stat = entry.stat()
            entries.append({
                "name": entry.name,
                "type": "file" if entry.is_file() else "dir",
                "size": stat.st_size if entry.is_file() else 0,
            })
    except FileNotFoundError:
        raise ToolError(f"Directory not found: {path}")
    except NotADirectoryError:
        raise ToolError(f"Not a directory: {path}")
    except OSError as exc:
        raise ToolError(f"Cannot list directory {path}: {exc}") from exc
    return {"entries": entries, "path": str(p.resolve())}


def make_file_ops_tools(security: "SecurityConfig") -> tuple[ToolDefinition, ToolDefinition, ToolDefinition]:
    """
    Build read_file, write_file, list_directory ToolDefinitions bound to a SecurityConfig.

    Returns:
        (READ_FILE_TOOL, WRITE_FILE_TOOL, LIST_DIRECTORY_TOOL)
    """
    read_tool = ToolDefinition(
        name="read_file",
        description="Read the contents of a file and return them as a string.",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the file to read"},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
        handler=lambda p: read_file(p["path"], security),
        when_to_use="To read any file before analyzing or modifying it",
    )

    write_tool = ToolDefinition(
        name="write_file",
        description="Write content to a file, creating it and any missing parent directories.",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to write to"},
                "content": {"type": "string", "description": "Content to write"},
            },
            "required": ["path", "content"],
            "additionalProperties": False,
        },
        handler=lambda p: write_file(p["path"], p["content"], security),
        when_to_use="To create or overwrite a file — always read first if the file already exists",
    )

    list_tool = ToolDefinition(
        name="list_directory",
        description="List the contents of a directory, returning name, type (file/dir), and size.",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory path to list"},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
        handler=lambda p: list_directory(p["path"], security),
        when_to_use="To explore directory structure and discover files",
    )

    return read_tool, write_tool, list_tool
