"""PostgreSQL implementation of ScheduledTaskRepo.

Stores scheduled tasks in the ``scheduled_tasks`` table with flat columns.
Uses croniter-based filtering for ``get_due_tasks()`` — fetches all
enabled tasks and evaluates each cron expression against last_run in
Python, avoiding the need for PG-native cron support.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from sqlalchemy.engine import Row

from croniter import croniter
from sqlalchemy import text

from nanoclaw.scheduler.repo import ScheduledTask, ScheduledTaskRepo
from nanoclaw.storage.db import get_session


class PgScheduledTaskRepo(ScheduledTaskRepo):
    """PostgreSQL-backed scheduled task repository.

    All methods use raw SQL via SQLAlchemy ``text()`` — the schema is
    simple enough that an ORM is unnecessary overhead.
    """

    async def create(self, task: ScheduledTask) -> ScheduledTask:
        """Persist a new task. ``task.id`` should be pre-generated."""
        if not task.id:
            # Generate a UUID-style ID if none provided
            import uuid
            task.id = uuid.uuid4().hex[:12]
        async with get_session() as s:
            await s.execute(
                text("""
                    INSERT INTO scheduled_tasks
                        (id, user_id, description, prompt, schedule,
                         enabled, created_at, agent_id, session_id)
                    VALUES
                        (:id, :user_id, :description, :prompt, :schedule,
                         :enabled, :created_at, :agent_id, :session_id)
                """),
                {
                    "id": task.id,
                    "user_id": task.user_id,
                    "description": task.description,
                    "prompt": task.prompt,
                    "schedule": task.schedule,
                    "enabled": task.enabled,
                    "created_at": task.created_at,
                    "agent_id": task.agent_id,
                    "session_id": task.session_id,
                },
            )
            await s.commit()
        return task

    async def get_due_tasks(self) -> list[ScheduledTask]:
        """Return all enabled tasks whose cron schedule is due.

        Fetches all enabled tasks from PG, then filters in Python using
        croniter. Each task's cron expression is checked against its
        last_run timestamp. O(N) where N = number of enabled tasks,
        acceptable for <100 scheduled tasks.
        """
        async with get_session() as s:
            rows = (
                await s.execute(
                    text("""
                        SELECT * FROM scheduled_tasks
                        WHERE enabled = TRUE
                    """)
                )
            ).fetchall()

        now = datetime.now(timezone.utc)
        due: list[ScheduledTask] = []
        for row in rows:
            task = self._row_to_task(row)
            try:
                cron = croniter(task.schedule, now)
                prev_run = cron.get_prev(datetime)
                if task.last_run is None or prev_run > datetime.fromisoformat(task.last_run):
                    due.append(task)
            except (ValueError, KeyError):
                continue  # Invalid cron expression — skip silently
        return due

    async def update_last_run(self, task_id: str, timestamp: str) -> None:
        async with get_session() as s:
            await s.execute(
                text("""
                    UPDATE scheduled_tasks
                    SET last_run = :ts::timestamptz
                    WHERE id = :id
                """),
                {"id": task_id, "ts": timestamp},
            )
            await s.commit()

    async def list_all(self) -> list[ScheduledTask]:
        async with get_session() as s:
            rows = (
                await s.execute(
                    text("""
                        SELECT * FROM scheduled_tasks
                        ORDER BY created_at DESC
                    """)
                )
            ).fetchall()
        return [self._row_to_task(r) for r in rows]

    async def get(self, task_id: str) -> ScheduledTask | None:
        async with get_session() as s:
            row = (
                await s.execute(
                    text("""
                        SELECT * FROM scheduled_tasks WHERE id = :id
                    """),
                    {"id": task_id},
                )
            ).fetchone()
        if row is None:
            return None
        return self._row_to_task(row)

    async def delete(self, task_id: str) -> None:
        async with get_session() as s:
            await s.execute(
                text("DELETE FROM scheduled_tasks WHERE id = :id"),
                {"id": task_id},
            )
            await s.commit()

    async def update(
        self, task_id: str, updates: dict[str, Any]
    ) -> ScheduledTask | None:
        """Partial update — only the specified fields are modified."""
        if not updates:
            return await self.get(task_id)

        # Whitelist allowed columns — prevent SQL injection
        _ALLOWED_COLUMNS = frozenset({
            "description", "prompt", "schedule", "enabled",
            "last_run", "agent_id", "session_id", "user_id",
        })
        for key in updates:
            if key not in _ALLOWED_COLUMNS:
                raise ValueError(f"Unknown column: {key!r}")

        set_clauses = ", ".join(
            f"{key} = :{key}" for key in updates
        )
        params = {**updates, "id": task_id}

        async with get_session() as s:
            await s.execute(
                text(f"""
                    UPDATE scheduled_tasks
                    SET {set_clauses}
                    WHERE id = :id
                """),
                params,
            )
            await s.commit()

        return await self.get(task_id)

    # ── Internal helpers ───────────────────────────────────────────

    def _row_to_task(self, row: Row) -> ScheduledTask:
        """Convert a SQLAlchemy row proxy to a ScheduledTask."""
        return ScheduledTask(
            id=row.id,
            user_id=row.user_id,
            description=row.description,
            prompt=row.prompt,
            schedule=row.schedule,
            enabled=row.enabled,
            created_at=row.created_at,
            last_run=(
                row.last_run.isoformat() if row.last_run else None
            ),
            agent_id=row.agent_id,
            session_id=row.session_id,
        )
