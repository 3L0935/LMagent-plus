"""
Tests for path sanitization in file_ops tools.
"""

from __future__ import annotations

import pytest

from core.config import SecurityConfig
from core.errors import ToolError
from core.tools.file_ops import make_file_ops_tools


@pytest.fixture
def default_security():
    return SecurityConfig()


@pytest.fixture
def tools(default_security):
    read_tool, write_tool, list_tool = make_file_ops_tools(default_security)
    return read_tool, write_tool, list_tool


class TestBlockedPaths:
    @pytest.mark.asyncio
    async def test_write_to_etc_blocked(self, tools):
        _, write_tool, _ = tools
        with pytest.raises(ToolError, match="Access denied"):
            await write_tool.handler({"path": "/etc/test_file", "content": "pwned"})

    @pytest.mark.asyncio
    async def test_write_to_ssh_blocked(self, tools):
        _, write_tool, _ = tools
        with pytest.raises(ToolError, match="Access denied"):
            await write_tool.handler({"path": "~/.ssh/authorized_keys", "content": "key"})

    @pytest.mark.asyncio
    async def test_read_etc_shadow_blocked(self, tools):
        read_tool, _, _ = tools
        with pytest.raises(ToolError, match="Access denied"):
            await read_tool.handler({"path": "/etc/shadow"})

    @pytest.mark.asyncio
    async def test_list_etc_blocked(self, tools):
        _, _, list_tool = tools
        with pytest.raises(ToolError, match="Access denied"):
            await list_tool.handler({"path": "/etc"})

    @pytest.mark.asyncio
    async def test_write_var_blocked(self, tools):
        _, write_tool, _ = tools
        with pytest.raises(ToolError, match="Access denied"):
            await write_tool.handler({"path": "/var/log/test", "content": "x"})

    @pytest.mark.asyncio
    async def test_gnupg_blocked(self, tools):
        _, write_tool, _ = tools
        with pytest.raises(ToolError, match="Access denied"):
            await write_tool.handler({"path": "~/.gnupg/test", "content": "x"})


class TestAllowedPaths:
    @pytest.mark.asyncio
    async def test_write_in_allowed_path_ok(self, tmp_path):
        security = SecurityConfig(allowed_paths=[str(tmp_path)])
        _, write_tool, _ = make_file_ops_tools(security)
        result = await write_tool.handler({"path": str(tmp_path / "test.txt"), "content": "hello"})
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_read_in_allowed_path_ok(self, tmp_path):
        target = tmp_path / "file.txt"
        target.write_text("content")
        security = SecurityConfig(allowed_paths=[str(tmp_path)])
        read_tool, _, _ = make_file_ops_tools(security)
        result = await read_tool.handler({"path": str(target)})
        assert result["content"] == "content"

    @pytest.mark.asyncio
    async def test_write_outside_allowed_path_blocked(self, tmp_path):
        security = SecurityConfig(allowed_paths=[str(tmp_path)])
        _, write_tool, _ = make_file_ops_tools(security)
        with pytest.raises(ToolError, match="not under any allowed path"):
            await write_tool.handler({"path": "/tmp/outside.txt", "content": "x"})

    @pytest.mark.asyncio
    async def test_no_restriction_when_allowed_paths_empty(self, tmp_path):
        """Empty allowed_paths = no restriction (backward compat)."""
        security = SecurityConfig(allowed_paths=[])
        read_tool, write_tool, _ = make_file_ops_tools(security)
        target = tmp_path / "ok.txt"
        target.write_text("data")
        result = await read_tool.handler({"path": str(target)})
        assert result["content"] == "data"
