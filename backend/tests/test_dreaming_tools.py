"""Tests for Dreaming Agent tool specs."""

from __future__ import annotations

from nanoclaw.dreaming import (
    LlmAnalyzeTool,
    ReadEvalLogsTool,
    ReadMemoryTool,
    WriteMemoryTool,
    register_dreaming_tools,
)
from nanoclaw.tools.registry import ToolRegistry


class TestToolSpecs:
    """Tool spec definitions should have correct names and parameters."""

    def _check(self, cls, name: str, params: list[str]) -> None:
        t = cls.__new__(cls)
        assert t.spec.name == name
        for p in params:
            assert p in t.spec.parameters["properties"]

    def test_read_eval_logs(self) -> None:
        self._check(ReadEvalLogsTool, "read_eval_logs", ["date", "session_id", "event_type", "limit"])

    def test_write_memory(self) -> None:
        self._check(WriteMemoryTool, "write_memory", ["type", "content", "tags", "confidence"])
        t = WriteMemoryTool.__new__(WriteMemoryTool)
        assert "type" in t.spec.parameters["required"]
        assert "content" in t.spec.parameters["required"]

    def test_read_memory(self) -> None:
        self._check(ReadMemoryTool, "read_memory", ["query", "type_filter", "tags", "top_k"])

    def test_llm_analyze(self) -> None:
        self._check(LlmAnalyzeTool, "llm_analyze", ["data", "instruction"])


class TestRegisterDreamingTools:
    """register_dreaming_tools() should register all 4 tools."""

    def test_registers_all_tools(self) -> None:
        reg = ToolRegistry()
        register_dreaming_tools(reg, "/tmp/eval", None, None)  # type: ignore[arg-type]
        names = [t.spec.name for t in reg._tools.values()]
        assert "read_eval_logs" in names
        assert "write_memory" in names
        assert "read_memory" in names
        assert "llm_analyze" in names

    def test_type_error_on_bad_registry(self) -> None:
        try:
            register_dreaming_tools({}, "/tmp", None, None)  # type: ignore[arg-type]
            assert False, "Should raise TypeError"
        except TypeError:
            pass
