"""
Path guard — validate filesystem paths against the security config.

Called by file_ops tools before any read/write/list operation.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from core.errors import ToolError

if TYPE_CHECKING:
    from core.config import SecurityConfig


def check_path(path: str, security: "SecurityConfig") -> None:
    """
    Validate a path against the security config.

    Raises ToolError if the path is blocked.

    Logic:
    - Resolve the path to absolute (expand ~ and resolve symlinks)
    - If allowed_paths is non-empty: path must be under one of them
    - Always: path must NOT be under any blocked_paths entry
    - Expand ~ in blocked_paths entries before comparison
    """
    resolved = Path(path).expanduser().resolve()

    # Check blocked paths first (always enforced)
    for blocked_str in security.blocked_paths:
        blocked = Path(blocked_str).expanduser().resolve()
        if resolved == blocked or blocked in resolved.parents:
            raise ToolError(
                f"Access denied: '{resolved}' is under blocked path '{blocked_str}'"
            )

    # If allowed_paths is configured, the path must be under one of them
    if security.allowed_paths:
        for allowed_str in security.allowed_paths:
            allowed = Path(allowed_str).expanduser().resolve()
            if resolved == allowed or allowed in resolved.parents:
                return
        raise ToolError(
            f"Access denied: '{resolved}' is not under any allowed path. "
            f"Allowed: {security.allowed_paths}"
        )
