"""Task-related data models.

Phase 1: Subtask/TaskPlan/EffectLogEntry/CheckpointState are defined here
but only used by simple-path ReAct (no multi-task dispatch yet).
Phase 2+ activates the full lifecycle including compensation and checkpoint.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nanoclaw.models.chat import Step


class TaskStatus(str, Enum):
    """Lifecycle status of a task or subtask.

    Flow:
      PENDING → RUNNING → SUCCEEDED
                         → FAILED → RETRYING (if retries remain)
                         → CANCELLED
      COMPENSATING → COMPENSATED
                   → COMPENSATION_FAILED
    """

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    RETRYING = "RETRYING"
    CANCELLED = "CANCELLED"
    COMPENSATING = "COMPENSATING"
    COMPENSATED = "COMPENSATED"
    COMPENSATION_FAILED = "COMPENSATION_FAILED"


@dataclass
class Subtask:
    """A single unit of work within a task plan.

    Forms a DAG via depends_on. trace captures the full ReAct execution
    for checker evaluation and trajectory logging.
    """

    id: str
    description: str
    status: TaskStatus = TaskStatus.PENDING
    # DAG dependency: list of subtask IDs that must complete first
    depends_on: list[str] = field(default_factory=list)
    # Tool names this subtask is expected to use (for checker routing)
    tools_needed: list[str] = field(default_factory=list)
    # ReAct execution trace (think/action/observation steps)
    trace: list[Step] = field(default_factory=list)
    # Compensation command to run if rollback is needed
    compensation: str | None = None
    max_retries: int = 3
    retry_count: int = 0
    result: str | None = None
    output_files: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass
class TaskPlan:
    """A complete plan consisting of multiple subtasks in a DAG."""

    session_id: str
    subtasks: list[Subtask] = field(default_factory=list)


@dataclass
class EffectLogEntry:
    """Runtime side-effect tracking for compensation and undo.

    Each entry records a concrete action (file create/edit, command run)
    so that compensation logic knows exactly what to revert.
    version field supports MVCC-lite for concurrent write detection.
    """

    task_id: str
    subtask_id: str
    action: str  # "create_file", "edit_file", "run_command"
    resource: str  # file path, URL, etc.
    metadata: dict = field(default_factory=dict)
    version: int = 1
    timestamp: float = 0.0


@dataclass
class CheckpointState:
    """Snapshot of agent state for checkpoint/restore.

    Captures both the LangGraph graph state and the TaskQueue snapshot,
    enabling crash recovery and pause/resume.
    """

    graph_state: dict
    queue_snapshot: dict | None = None
    node_name: str = ""
    timestamp: float = 0.0
