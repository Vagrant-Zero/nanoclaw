"""PostgreSQL implementation of Checkpointer.

Uses the ``sessions.serialized_state`` JSONB column to store and
retrieve graph state checkpoints. Each session has at most one active
checkpoint — subsequent saves overwrite the previous one.
"""

from __future__ import annotations

import json

from sqlalchemy import text

from nanoclaw.models.task import CheckpointState
from nanoclaw.storage._jsonb import deserialize_jsonb
from nanoclaw.storage.checkpointer import Checkpointer
from nanoclaw.storage.db import get_session


class PgCheckpointer(Checkpointer):
    """PostgreSQL-backed checkpoint storage.

    Relies on the ``sessions`` table having a ``serialized_state``
    JSONB column. Checkpoints are scoped to sessions — one checkpoint
    per session (the latest graph state).
    """

    async def save(self, session_id: str, state: CheckpointState) -> None:
        async with get_session() as s:
            await s.execute(
                text("""
                    UPDATE sessions
                    SET serialized_state = CAST(:state AS JSONB)
                    WHERE id = :session_id
                """),
                {
                    "session_id": session_id,
                    "state": json.dumps(state.to_dict()),
                },
            )
            await s.commit()

    async def load(self, session_id: str) -> CheckpointState | None:
        async with get_session() as s:
            row = (
                await s.execute(
                    text("""
                        SELECT serialized_state FROM sessions
                        WHERE id = :id
                    """),
                    {"id": session_id},
                )
            ).fetchone()
        if row is None or row.serialized_state is None:
            return None
        return CheckpointState.from_dict(deserialize_jsonb(row.serialized_state))

    async def list_sessions(self) -> list[str]:
        async with get_session() as s:
            rows = (
                await s.execute(
                    text("""
                        SELECT id FROM sessions
                        WHERE serialized_state IS NOT NULL
                    """)
                )
            ).fetchall()
        return [row.id for row in rows]
