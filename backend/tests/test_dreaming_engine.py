"""Tests for Dreaming Engine — tool chain extraction."""

from __future__ import annotations

from nanoclaw.dreaming import extract_tool_chains


class TestExtractToolChains:
    """extract_tool_chains() — sliding window over tool_call events."""

    def test_empty_events(self) -> None:
        assert extract_tool_chains([]) == {}

    def test_no_tool_call_events(self) -> None:
        events = [{"type": "task_start", "data": {"session_id": "s1"}}]
        assert extract_tool_chains(events) == {}

    def test_single_session(self) -> None:
        events = [
            {"type": "tool_call", "data": {"session_id": "s1", "tool_name": "read_file"}},
            {"type": "tool_call", "data": {"session_id": "s1", "tool_name": "grep"}},
            {"type": "tool_call", "data": {"session_id": "s1", "tool_name": "file_edit"}},
        ]
        chains = extract_tool_chains(events, chain_length=2)
        assert chains == {("read_file", "grep"): 1, ("grep", "file_edit"): 1}

    def test_cross_session_sum(self) -> None:
        """Same pattern in two sessions should be counted twice."""
        events = [
            {"type": "tool_call", "data": {"session_id": "s1", "tool_name": "read_file"}},
            {"type": "tool_call", "data": {"session_id": "s1", "tool_name": "grep"}},
            {"type": "tool_call", "data": {"session_id": "s2", "tool_name": "read_file"}},
            {"type": "tool_call", "data": {"session_id": "s2", "tool_name": "grep"}},
        ]
        chains = extract_tool_chains(events, chain_length=2)
        assert chains[("read_file", "grep")] == 2

    def test_triple_chain(self) -> None:
        """Chain length 3."""
        events = [
            {"type": "tool_call", "data": {"session_id": "s1", "tool_name": "read_file"}},
            {"type": "tool_call", "data": {"session_id": "s1", "tool_name": "grep"}},
            {"type": "tool_call", "data": {"session_id": "s1", "tool_name": "file_edit"}},
        ]
        chains = extract_tool_chains(events, chain_length=3)
        assert chains == {("read_file", "grep", "file_edit"): 1}

    def test_ignores_missing_fields(self) -> None:
        """Events missing session_id or tool_name should be ignored."""
        events = [
            {"type": "tool_call", "data": {"tool_name": "read_file"}},
            {"type": "tool_call", "data": {"session_id": "s1"}},
        ]
        assert extract_tool_chains(events) == {}
