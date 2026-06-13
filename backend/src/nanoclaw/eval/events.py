"""Evaluation event types — structured dataclasses for every loggable event.

These are serialised as JSONL by ``EventLogger`` (Task 5).  Each dataclass
carries the fields relevant to its event type.
"""

from __future__ import annotations

from dataclasses import dataclass, field


# ── Event type string constants ─────────────────────────────────────

EVENT_TASK_START = "task_start"
EVENT_TASK_END = "task_end"
EVENT_TOOL_CALL = "tool_call"
EVENT_USER_FEEDBACK = "user_feedback"
EVENT_CONTEXT_STATS = "context_stats"
EVENT_LLM_CALL = "llm_call"


# ── Event dataclasses ────────────────────────────────────────────────


@dataclass
class TaskStartEvent:
    """Logged when a task begins (after routing)."""

    session_id: str
    task_id: str
    description: str
    subtask_count: int
    created_at: float = 0.0


@dataclass
class TaskEndEvent:
    """Logged when a task or subtask completes."""

    session_id: str
    task_id: str
    success: bool
    result_summary: str
    duration_ms: float
    error: str | None = None


@dataclass
class ToolCallEvent:
    """Logged after each tool invocation within a ReAct loop."""

    session_id: str
    task_id: str
    tool_name: str
    args_summary: str
    result_summary: str
    duration_ms: float


@dataclass
class UserFeedbackEvent:
    """Logged when the user confirms or rejects a memory entry."""

    session_id: str
    feedback_type: str  # "confirm" | "reject" | "dismiss"
    content: str
    memory_entry_id: str | None = None


@dataclass
class ContextStatsEvent:
    """Logged after a context compression operation."""

    session_id: str
    total_tokens: int
    compression_count: int
    tokens_before: int
    tokens_after: int


@dataclass
class LlmCallEvent:
    """Logged after each LLM invocation."""

    session_id: str
    task_id: str
    model: str
    input_tokens: int
    output_tokens: int
    duration_ms: float
