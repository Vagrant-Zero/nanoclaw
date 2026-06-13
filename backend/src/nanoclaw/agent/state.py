"""Agent state definition using TypedDict."""

from __future__ import annotations

from typing import Annotated, Sequence, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages

from nanoclaw.agent.checker.checker import CheckerFeedback
from nanoclaw.agent.checker.iteration_budget import IterationBudget
from nanoclaw.agent.checker.trajectory_logger import TrajectoryLogger
from nanoclaw.agent.worker_pool import WorkerPool
from nanoclaw.models.task import TaskPlan
from nanoclaw.storage.session_repo import SessionRepository
from nanoclaw.storage.task_queue import TaskQueue
from nanoclaw.eval.logger import EventLogger
from nanoclaw.memory.reflection import ReflectionEngine
from nanoclaw.tools.registry import ToolRegistry


class AgentState(TypedDict):
    """State passed between nodes in the agent graph.

    messages:     LangChain message history (auto-merged by add_messages reducer)
    session_id:   Current conversation session ID
    task_id:      "root" (simple path) or "task_xxx" (subtask, Phase 2+)
    session_repo: Reference to session storage for history persistence
    """

    messages: Annotated[Sequence[AnyMessage], add_messages]
    session_id: str | None
    task_id: str | None
    session_repo: SessionRepository | None


class SupervisorState(AgentState):
    """Extended state for the full Supervisor graph (Phase 2).

    Adds multi-task execution fields required by the complex path:
    planner, dispatch, await_node, collect, and checker nodes.
    """

    # Tool registry (kept separate from AgentState to keep simple path clean)
    tool_registry: ToolRegistry | None

    # Phase 2 multi-task execution fields
    task_queue: TaskQueue | None          # DAG-aware task queue
    plan: TaskPlan | None                 # Current execution plan (after planner)
    worker_pool: WorkerPool | None        # Worker pool instance
    worker_results: dict[str, str] | None  # task_id -> result (populated by collect)
    errors: list[str] | None              # Error messages accumulated during execution

    # Checker subsystem fields
    checker_feedback: CheckerFeedback | None   # Check failure feedback (for re-plan)
    iteration_budget: IterationBudget | None   # Cascading iteration limits
    trajectory_logger: TrajectoryLogger | None  # Trajectory file logger

    # Phase 3: Memory & Evaluation fields
    event_logger: EventLogger | None               # Evaluation event logging
    reflection_engine: ReflectionEngine | None     # Post-task reflection
