"""Tests for cli.main — pure helper functions (no Textual runtime needed)."""

from __future__ import annotations

import pytest

from cli.main import format_tool_result, parse_slash_command


# ---------------------------------------------------------------------------
# parse_slash_command
# ---------------------------------------------------------------------------

class TestParseSlashCommand:
    def test_help(self):
        assert parse_slash_command("/help") == ("help", [])

    def test_agent_with_arg(self):
        assert parse_slash_command("/agent coder") == ("agent", ["coder"])

    def test_agent_no_arg(self):
        assert parse_slash_command("/agent") == ("agent", [])

    def test_stop(self):
        assert parse_slash_command("/stop") == ("stop", [])

    def test_clear(self):
        assert parse_slash_command("/clear") == ("clear", [])

    def test_model_with_arg(self):
        assert parse_slash_command("/model gpt-4o") == ("model", ["gpt-4o"])

    def test_multiple_args(self):
        cmd, args = parse_slash_command("/foo bar baz qux")
        assert cmd == "foo"
        assert args == ["bar", "baz", "qux"]

    def test_case_normalised_to_lower(self):
        assert parse_slash_command("/HELP") == ("help", [])
        assert parse_slash_command("/Agent Coder") == ("agent", ["Coder"])

    def test_not_a_slash_command(self):
        assert parse_slash_command("hello world") == ("", [])

    def test_empty_string(self):
        assert parse_slash_command("") == ("", [])

    def test_only_slash(self):
        # "/" alone has no command name after stripping "/"
        cmd, args = parse_slash_command("/")
        assert cmd == ""
        assert args == []

    def test_leading_whitespace_stripped(self):
        assert parse_slash_command("  /help  ") == ("help", [])


# ---------------------------------------------------------------------------
# format_tool_result
# ---------------------------------------------------------------------------

class TestFormatToolResult:
    def test_stdout_returned(self):
        result = format_tool_result("bash", {"stdout": "hello world", "returncode": 0})
        assert "hello world" in result
        assert result.startswith("stdout:")

    def test_stdout_truncated_at_400(self):
        long = "x" * 500
        result = format_tool_result("bash", {"stdout": long, "returncode": 0})
        assert len(result) <= 450  # "stdout: " + 400 chars + "…"
        assert result.endswith("…")

    def test_stderr_shown_when_no_stdout(self):
        result = format_tool_result("bash", {"stdout": "", "stderr": "oops", "returncode": 1})
        assert "oops" in result
        assert result.startswith("stderr:")

    def test_error_key(self):
        result = format_tool_result("bash", {"error": "file not found"})
        assert "file not found" in result
        assert result.startswith("error:")

    def test_stdout_preferred_over_stderr(self):
        result = format_tool_result(
            "bash", {"stdout": "ok output", "stderr": "some warning", "returncode": 0}
        )
        assert "ok output" in result
        assert result.startswith("stdout:")

    def test_arbitrary_dict_json_encoded(self):
        result = format_tool_result("read_file", {"content": "hello", "path": "/tmp/f"})
        assert "hello" in result

    def test_empty_dict(self):
        result = format_tool_result("git_status", {})
        assert isinstance(result, str)

    def test_truncates_json_at_400(self):
        big = {"data": "y" * 500}
        result = format_tool_result("read_file", big)
        assert len(result) <= 405
