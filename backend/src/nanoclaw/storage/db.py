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

import pathlib
from nanoclaw.config import settings

_engine = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_db_url() -> str:
    """Return the database URL from settings, with a sensible fallback."""
    return settings.db_url or "postgresql+asyncpg://nanoclaw:nanoclaw_dev@localhost:5432/nanoclaw"


async def init_db() -> None:
    """Initialize the async engine and sessionmaker. Called at startup.\n\nVerifies the connection with a ``SELECT 1`` query so failures\nsurface immediately at startup (fail fast).\n"""
    global _engine, _sessionmaker
    if _engine is not None:
        return  # Already initialized
    url = get_db_url()
    _engine = create_async_engine(url, pool_size=5, max_overflow=10)
    _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    # Verify connection — crash now if PostgreSQL is unreachable
    async with _sessionmaker() as conn:
        await conn.execute(text("SELECT 1"))

    # Run migrations — create tables if they don't exist
    _sql_path = pathlib.Path(__file__).resolve().parent.parent.parent / "migrations" / "init.sql"
    if _sql_path.exists():
        _sql = _sql_path.read_text()
        # Split by semicolons and execute each statement separately
        for _stmt in _sql.split(";"):
            _stmt = _stmt.strip()
            if _stmt:
                async with _sessionmaker() as _conn:
                    await _conn.execute(text(_stmt + ";"))
                


async def close_db() -> None:
    """Dispose the engine. Called at shutdown."""
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None


def get_session() -> AsyncSession:
    """Return a new AsyncSession from the sessionmaker.

    Must be called after ``init_db()`` has completed successfully.
    Raises RuntimeError if ``init_db()`` was never called.
    """
    if _sessionmaker is None:
        msg = (
            "Database not initialized. Either set NANOCLAW_DB_URL and ensure "
            "PostgreSQL is running, or unset it to use in-memory mode."
        )
        raise RuntimeError(msg)
    return _sessionmaker()
