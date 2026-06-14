"""PostgreSQL implementation of TaskRepository.

Stores task plans in the ``task_plans`` table with a JSONB ``data`` column.
Uses read-modify-write pattern for subtask updates since the entire plan
is stored as a single JSONB row.
"""

from __future__ import annotations

import json
import time

from sqlalchemy import text

from nanoclaw.models.task import Subtask, TaskPlan
from nanoclaw.storage.db import get_session
from nanoclaw.storage.task_repo import TaskRepository


class PgTaskRepo(TaskRepository):
    """PostgreSQL-backed task plan storage.

    Each plan is stored in the ``task_plans`` table:
      - ``session_id``: TEXT (part of composite PK)
      - ``plan_id``: TEXT (part of composite PK)
      - ``data``: JSONB (full serialized plan)
      - ``created_at``: DOUBLE PRECISION
    """

    async def save_plan(self, session_id: str, plan: TaskPlan) -> None:
        async with get_session() as s:
            await s.execute(
                text("""
                    INSERT INTO task_plans (session_id, plan_id, data, created_at)
                    VALUES (:session_id, :plan_id, :data::jsonb, :created_at)
                    ON CONFLICT (session_id, plan_id)
                    DO UPDATE SET data = :data::jsonb
                """),
                {
                    "session_id": session_id,
                    "plan_id": plan.id,
                    "data": json.dumps(plan.to_dict()),
                    "created_at": time.time(),
                },
            )
            await s.commit()

    async def get_plan(self, session_id: str) -> TaskPlan | None:
        async with get_session() as s:
            row = (
                await s.execute(
                    text("""
                        SELECT data FROM task_plans
                        WHERE session_id = :session_id
                        ORDER BY created_at DESC LIMIT 1
                    """),
                    {"session_id": session_id},
                )
            ).fetchone()
        if row is None:
            return None
        return TaskPlan.from_dict(json.loads(row.data))

    async def update_subtask(self, session_id: str, subtask: Subtask) -> None:
        """Read current plan, find and replace the subtask, write back.

        Uses read-modify-write since the full plan is stored as a single JSONB row.
        For a single-user personal AI assistant, write contention is negligible.
        """
        plan = await self.get_plan(session_id)
        if plan is None:
            msg = f"No plan found for session {session_id!r}"
            raise ValueError(msg)
        for i, st in enumerate(plan.subtasks):
            if st.id == subtask.id:
                plan.subtasks[i] = subtask
                break
        else:
            msg = f"Subtask {subtask.id!r} not found in plan for session {session_id!r}"
            raise ValueError(msg)
        await self.save_plan(session_id, plan)
