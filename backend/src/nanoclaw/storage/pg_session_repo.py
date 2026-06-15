"""PostgreSQL implementation of SessionRepository.

Stores session data in the ``sessions`` table with JSONB columns for
message history and serialized state.

All operations use raw SQL via SQLAlchemy ``text()`` — no ORM models
needed for this simple schema.
"""

from __future__ import annotations

import json

from sqlalchemy import text

from nanoclaw.models.chat import ChatMessage, Session
from nanoclaw.storage._jsonb import deserialize_jsonb_list
from nanoclaw.storage.db import get_session
from nanoclaw.storage.session_repo import SessionRepository


class PgSessionRepo(SessionRepository):
    """PostgreSQL-backed session storage.

    Each session is stored as a single row in the ``sessions`` table:
      - ``id``: TEXT PRIMARY KEY
      - ``created_at``: DOUBLE PRECISION
      - ``history``: JSONB array of message dicts
      - ``active_plan_id``: TEXT (nullable)
      - ``serialized_state``: JSONB (nullable, for checkpointing)
    """

    async def create(self, session: Session) -> Session:
        async with get_session() as s:
            await s.execute(
                text("""
                    INSERT INTO sessions (id, created_at, history, active_plan_id)
                    VALUES (:id, :created_at, CAST(:history AS JSONB), :active_plan_id)
                """),
                {
                    "id": session.id,
                    "created_at": session.created_at,
                    "history": json.dumps(
                        [m.to_dict() for m in session.messages]
                    ),
                    "active_plan_id": (
                        session.active_plan.id if session.active_plan else None
                    ),
                },
            )
            await s.commit()
        return session

    async def get(self, session_id: str) -> Session | None:
        async with get_session() as s:
            row = (
                await s.execute(
                    text("""
                        SELECT id, created_at, history, active_plan_id
                        FROM sessions
                        WHERE id = :id
                    """),
                    {"id": session_id},
                )
            ).fetchone()
        if row is None:
            return None
        raw = deserialize_jsonb_list(row.history)
        messages = [ChatMessage.from_dict(m) for m in raw]
        # active_plan_id is read for round-trip preservation; the full
        # TaskPlan is loaded separately via TaskRepository.get_plan().
        return Session(
            id=row.id,
            created_at=row.created_at,
            messages=messages,
        )

    async def append_message(self, session_id: str, msg: ChatMessage) -> None:
        """Atomically append a message to the session history.

        Uses a single UPDATE with jsonb concatenation (the || operator)
        instead of the previous read-modify-write pattern. This eliminates
        the race condition where two concurrent appends could overwrite
        each other's changes.
        """
        async with get_session() as s:
            result = await s.execute(
                text("""
                    UPDATE sessions
                    SET history = history || CAST(:new_msg AS JSONB)
                    WHERE id = :id
                """),
                {"id": session_id, "new_msg": json.dumps([msg.to_dict()])},
            )
            if result.rowcount == 0:
                msg_text = f"Session {session_id!r} not found"
                raise ValueError(msg_text)
            await s.commit()

    async def get_history(self, session_id: str) -> list[ChatMessage]:
        async with get_session() as s:
            row = (
                await s.execute(
                    text("SELECT history FROM sessions WHERE id = :id"),
                    {"id": session_id},
                )
            ).fetchone()
        if row is None:
            msg_text = f"Session {session_id!r} not found"
            raise ValueError(msg_text)
        raw = deserialize_jsonb_list(row.history)
