"""Tests for core.router.AgentRouter (heuristic routing)."""

from __future__ import annotations

import pytest

from core.router import AgentRouter


@pytest.fixture
def router() -> AgentRouter:
    return AgentRouter()


class TestAgentRouterCoder:
    def test_routes_bug_to_coder(self, router):
        assert router.route("fix the bug in auth.py") == "coder"

    def test_routes_code_to_coder(self, router):
        assert router.route("write code for the login feature") == "coder"

    def test_routes_git_to_coder(self, router):
        assert router.route("run git status and show me the diff") == "coder"

    def test_routes_refactor_to_coder(self, router):
        assert router.route("refactor the database module") == "coder"

    def test_routes_debug_to_coder(self, router):
        assert router.route("debug the failing test") == "coder"


class TestAgentRouterResearch:
    def test_routes_research_to_research(self, router):
        assert router.route("research best practices for API design") == "research"

    def test_routes_analyze_to_research(self, router):
        assert router.route("analyze the performance of these algorithms") == "research"

    def test_routes_compare_to_research(self, router):
        assert router.route("compare these two approaches") == "research"

    def test_routes_explain_to_research(self, router):
        assert router.route("explain how the auth middleware works") == "research"


class TestAgentRouterWriter:
    def test_routes_write_to_writer(self, router):
        assert router.route("write a blog post about async Python") == "writer"

    def test_routes_draft_to_writer(self, router):
        assert router.route("draft a README for this project") == "writer"

    def test_routes_summarize_to_writer(self, router):
        assert router.route("summarize this document") == "writer"

    def test_routes_rephrase_to_writer(self, router):
        assert router.route("rephrase this paragraph to be more concise") == "writer"


class TestAgentRouterFallback:
    def test_unknown_task_defaults_to_assistant(self, router):
        assert router.route("what time is it?") == "assistant"

    def test_empty_task_defaults_to_assistant(self, router):
        assert router.route("") == "assistant"

    def test_generic_question_defaults_to_assistant(self, router):
        assert router.route("help me with something") == "assistant"


class TestAgentRouterFirstMatchWins:
    def test_first_matching_rule_wins(self, router):
        # "code" matches coder before "analyze" would match research
        result = router.route("analyze the code quality")
        assert result == "coder"

    def test_case_insensitive(self, router):
        assert router.route("FIX THE BUG") == "coder"
        assert router.route("WRITE A BLOG") == "writer"
        assert router.route("RESEARCH this topic") == "research"
