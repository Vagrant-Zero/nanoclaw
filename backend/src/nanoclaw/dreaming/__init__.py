"""Dreaming subsystem — background experience mining and consolidation."""
from __future__ import annotations

from nanoclaw.dreaming.cron import DreamingCronTrigger
from nanoclaw.dreaming.engine import DreamingEngine, extract_tool_chains
from nanoclaw.dreaming.tools import (
    LlmAnalyzeTool,
    ReadEvalLogsTool,
    ReadMemoryTool,
    WriteMemoryTool,
    register_dreaming_tools,
)

__all__ = [
    "DreamingCronTrigger",
    "DreamingEngine",
    "LlmAnalyzeTool",
    "ReadEvalLogsTool",
    "ReadMemoryTool",
    "WriteMemoryTool",
    "extract_tool_chains",
    "register_dreaming_tools",
]
