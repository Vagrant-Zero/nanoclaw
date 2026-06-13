"""Evaluation subsystem — structured event logging for observability."""
from __future__ import annotations

from nanoclaw.eval.events import (
    ContextStatsEvent,
    LlmCallEvent,
    TaskEndEvent,
    TaskStartEvent,
    ToolCallEvent,
    UserFeedbackEvent,
)
from nanoclaw.eval.logger import EventLogger

__all__ = [
    "ContextStatsEvent",
    "EventLogger",
    "LlmCallEvent",
    "TaskEndEvent",
    "TaskStartEvent",
    "ToolCallEvent",
    "UserFeedbackEvent",
]
