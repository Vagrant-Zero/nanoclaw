"""Shared async SQLAlchemy engine and session factory.

Module-level globals are standard practice for FastAPI apps.
Engine is created once at startup and disposed at shutdown.

Usage:
    from nanoclaw.storage.db import init_db, close_db, get_session

    await init_db()
    async with get_session() as session:
        result = await session.execute(...)
    await close_db()
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from nanoclaw.config import settings

_engine = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_db_url() -> str:
    """Return the database URL from settings, with a sensible fallback."""
    return settings.db_url or "postgresql+asyncpg://nanoclaw:nanoclaw_dev@localhost:5432/nanoclaw"


async def init_db() -> None:
    """Initialize the async engine and sessionmaker. Called at startup."""
    global _engine, _sessionmaker
    if _engine is not None:
        return  # Already initialized
    url = get_db_url()
    _engine = create_async_engine(url, pool_size=5, max_overflow=10)
    _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)


async def close_db() -> None:
    """Dispose the engine. Called at shutdown."""
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None


def get_session() -> AsyncSession:
    """Return a new AsyncSession from the sessionmaker.

    Raises RuntimeError if init_db() has not been called.
    The caller should use ``async with`` to manage the session lifecycle.
    """
    if _sessionmaker is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    return _sessionmaker()
