"""
File operation tools — read, write, list directory.
"""

from __future__ import annotations

import os
from pathlib import Path

from core.errors import ToolError
from core.tool_registry import ToolDefinition


async def read_file(path: str) -> dict:
    """Read a file and return its content."""
    p = Path(path)
    try:
        content = p.read_text(errors="replace")
    except FileNotFoundError:
        raise ToolError(f"File not found: {path}")
    except OSError as exc:
        raise ToolError(f"Cannot read file {path}: {exc}") from exc
    return {"content": content, "path": str(p.resolve())}


async def write_file(path: str, content: str) -> dict:
    """Write content to a file (creates parent dirs as needed)."""
    p = Path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    except OSError as exc:
        raise ToolError(f"Cannot write file {path}: {exc}") from exc
    return {"success": True, "path": str(p.resolve())}


async def list_directory(path: str) -> dict:
    """List directory entries with name, type, and size."""
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


READ_FILE_TOOL = ToolDefinition(
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
    handler=lambda p: read_file(p["path"]),
    when_to_use="To read any file before analyzing or modifying it",
)

WRITE_FILE_TOOL = ToolDefinition(
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
    handler=lambda p: write_file(p["path"], p["content"]),
    when_to_use="To create or overwrite a file — always read first if the file already exists",
)

LIST_DIRECTORY_TOOL = ToolDefinition(
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
    handler=lambda p: list_directory(p["path"]),
    when_to_use="To explore directory structure and discover files",
)
