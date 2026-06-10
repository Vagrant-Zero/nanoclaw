"""Session repository abstraction and in-memory implementation.

SessionRepository manages conversation sessions: creation, retrieval,
message history append/query. Used by the agent graph to persist
conversation state across turns.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from nanoclaw.models.chat import ChatMessage, Session


class SessionRepository(ABC):
    """Abstract session storage.

    Implementations: MemorySessionRepo (in-process dict),
    PgSessionRepo (PostgreSQL via SQLAlchemy, Phase 5+).
    """

    @abstractmethod
    async def create(self, session: Session) -> Session:
        """Persist a new session and return it."""

    @abstractmethod
    async def get(self, session_id: str) -> Session | None:
        """Retrieve a session by ID, or None if not found."""

    @abstractmethod
    async def append_message(self, session_id: str, msg: ChatMessage) -> None:
        """Append a message to the session's history."""

    @abstractmethod
    async def get_history(self, session_id: str) -> list[ChatMessage]:
        """Retrieve all messages for a session, ordered by creation."""


class MemorySessionRepo(SessionRepository):
    """In-memory session storage using a dict.

    All data is lost on process restart. Suitable for development
    and testing before PostgreSQL is available.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}

    async def create(self, session: Session) -> Session:
        self._sessions[session.id] = session
        return session

    async def get(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)

    async def append_message(self, session_id: str, msg: ChatMessage) -> None:
        session = self._sessions.get(session_id)
        if session is None:
            msg = f"Session {session_id!r} not found"
            raise ValueError(msg)
        session.messages.append(msg)

    async def get_history(self, session_id: str) -> list[ChatMessage]:
        session = self._sessions.get(session_id)
        if session is None:
            msg = f"Session {session_id!r} not found"
            raise ValueError(msg)
        return list(session.messages)
