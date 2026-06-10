"""Task repository abstraction and in-memory implementation.

TaskRepository persists task plans and subtask state.
Phase 1 uses MemoryTaskRepo (in-process dict); Phase 5+ adds PgTaskRepo.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from nanoclaw.models.task import Subtask, TaskPlan


class TaskRepository(ABC):
    """Abstract task plan storage."""

    @abstractmethod
    async def save_plan(self, session_id: str, plan: TaskPlan) -> None:
        """Persist or overwrite a task plan for a session."""

    @abstractmethod
    async def get_plan(self, session_id: str) -> TaskPlan | None:
        """Retrieve the active plan for a session, or None."""

    @abstractmethod
    async def update_subtask(self, session_id: str, subtask: Subtask) -> None:
        """Update a single subtask's state within the session's plan."""


class MemoryTaskRepo(TaskRepository):
    """In-memory task plan storage.

    Maintains a dict of session_id -> TaskPlan. Suitable for development.
    """

    def __init__(self) -> None:
        self._plans: dict[str, TaskPlan] = {}

    async def save_plan(self, session_id: str, plan: TaskPlan) -> None:
        self._plans[session_id] = plan

    async def get_plan(self, session_id: str) -> TaskPlan | None:
        return self._plans.get(session_id)

    async def update_subtask(self, session_id: str, subtask: Subtask) -> None:
        plan = self._plans.get(session_id)
        if plan is None:
            msg = f"No plan found for session {session_id!r}"
            raise ValueError(msg)
        for i, s in enumerate(plan.subtasks):
            if s.id == subtask.id:
                plan.subtasks[i] = subtask
                return
        msg = f"Subtask {subtask.id!r} not found in plan for session {session_id!r}"
        raise ValueError(msg)
