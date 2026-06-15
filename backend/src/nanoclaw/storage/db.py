"""Shared async SQLAlchemy engine and session factory.

Module-level globals are standard practice for FastAPI apps.
Engine is created once at startup and disposed at shutdown.

Multi-worker safety (uvicorn --workers > 1):
- Each child process forks after the lifespan start hook, so every
  worker gets its own copy of _engine and _sessionmaker globals.
- No shared state across workers; no synchronization needed.

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


def _split_sql_statements(sql: str) -> list[str]:
    """Split SQL text into individual statements, preserving semicolons
    inside PL/pgSQL function bodies ($$-delimited strings).

    Uses a simple state-machine that tracks whether we are inside a
    ``$$ ... $$`` dollar-quoted block where semicolons are part of the
    function body rather than statement terminators.
    """
    statements: list[str] = []
    current: list[str] = []
    in_dollar = False

    i = 0
    while i < len(sql):
        ch = sql[i]

        # Track $$ blocks
        if ch == "$" and i + 1 < len(sql) and sql[i + 1] == "$":
            if not in_dollar:
                in_dollar = True
                current.append("$$")
                i += 2
                continue
            elif i + 2 < len(sql) and sql[i + 2] != "$":
                in_dollar = False
                current.append("$$")
                i += 2
                continue

        if ch == ";" and not in_dollar:
            stmt = "".join(current).strip()
            if stmt:
                statements.append(stmt)
            current = []
        else:
            current.append(ch)
        i += 1

    # Last statement (no trailing semicolon)
    stmt = "".join(current).strip()
    if stmt and stmt not in ("", " "):
        statements.append(stmt)

    return statements


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
        for _stmt in _split_sql_statements(_sql):
            async with _sessionmaker() as _conn:
                async with _conn.begin():
                    await _conn.execute(text(_stmt))

    # Warm-up the async pool: establish connections now so the first
    # request does not pay lazy-connect latency (~50ms+ per connection).
    for _ in range(5):  # pool_size
        async with _sessionmaker() as _warmup:
            await _warmup.execute(text("SELECT 1"))


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
