"""FastAPI dependency injection — returns Memory or PG/Redis implementations.

Module-level singleton holders are populated once at first access.
The implementation choice (Memory vs PG/Redis) is driven by the presence
of ``NANOCLAW_DB_URL`` and ``NANOCLAW_REDIS_URL`` environment variables.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from langchain_openai import ChatOpenAI

from nanoclaw.agent.supervisor_graph import create_supervisor
from nanoclaw.config import settings
from nanoclaw.storage.checkpointer import Checkpointer, LocalFileCheckpointer
from nanoclaw.storage.session_repo import SessionRepository
from nanoclaw.storage.task_queue import TaskQueue
from nanoclaw.storage.task_repo import TaskRepository
from nanoclaw.tools.registry import ToolRegistry

# Module-level singleton holders — set once at startup
_session_repo: SessionRepository | None = None
_task_repo: TaskRepository | None = None
_checkpointer: Checkpointer | None = None


def is_production() -> bool:
    """Return True if PostgreSQL is configured."""
    return settings.db_url is not None


def get_session_repo() -> SessionRepository:
    """Return SessionRepository: PgSessionRepo if db_url is set, else MemorySessionRepo."""
    global _session_repo
    if _session_repo is not None:
        return _session_repo
    if is_production():
        from nanoclaw.storage.pg_session_repo import PgSessionRepo
        _session_repo = PgSessionRepo()
    else:
        from nanoclaw.storage.session_repo import MemorySessionRepo
        _session_repo = MemorySessionRepo()
    return _session_repo


def get_task_repo() -> TaskRepository:
    """Return TaskRepository: PgTaskRepo if db_url is set, else MemoryTaskRepo."""
    global _task_repo
    if _task_repo is not None:
        return _task_repo
    if is_production():
        from nanoclaw.storage.pg_task_repo import PgTaskRepo
        _task_repo = PgTaskRepo()
    else:
        from nanoclaw.storage.task_repo import MemoryTaskRepo
        _task_repo = MemoryTaskRepo()
    return _task_repo


def get_checkpointer() -> Checkpointer:
    """Return Checkpointer: PgCheckpointer if db_url is set, else LocalFileCheckpointer."""
    global _checkpointer
    if _checkpointer is not None:
        return _checkpointer
    if is_production():
        from nanoclaw.storage.pg_checkpointer import PgCheckpointer
        _checkpointer = PgCheckpointer()
    else:
        _checkpointer = LocalFileCheckpointer()
    return _checkpointer


def get_scheduled_task_repo() -> Any:
    """Return ScheduledTaskRepo: PgScheduledTaskRepo if db_url is set, else Memory."""
    if is_production():
        from nanoclaw.scheduler.pg_repo import PgScheduledTaskRepo
        return PgScheduledTaskRepo()
    from nanoclaw.scheduler.repo import MemoryScheduledTaskRepo
    return MemoryScheduledTaskRepo()


def create_queue(session_id: str) -> TaskQueue:
    """Factory: returns RedisQueue if redis_url is set, else MemoryQueue.

    Unlike the repo singletons, queue instances are per-session and must
    be created fresh each time.
    """
    if settings.redis_url is not None:
        from nanoclaw.storage.redis_queue import RedisQueue
        return RedisQueue(session_id)
    from nanoclaw.storage.task_queue import MemoryQueue
    return MemoryQueue()


@lru_cache
def get_llm() -> Any:
    """Create the LangChain chat model (singleton).

    Uses langchain-openai with DeepSeek-compatible base URL.
    The model instance is cached across requests.
    """
    return ChatOpenAI(
        model=settings.llm_model,
        api_key=settings.openai_api_key,
        base_url=settings.llm_base_url,
        temperature=0.7,
    )


@lru_cache
def get_tool_registry() -> ToolRegistry:
    """Create and populate the tool registry (singleton).

    All tools are registered once and never modified per-request,
    ensuring the KV cache prefix remains stable.
    """
    from nanoclaw.tools.file_ops import ReadFileTool
    from nanoclaw.tools.shell import RunShellTool
    from nanoclaw.tools.web_search import WebSearchTool

    registry = ToolRegistry()
    registry.register(ReadFileTool())
    registry.register(RunShellTool())
    registry.register(WebSearchTool())
    return registry


@lru_cache
def get_supervisor() -> Any:
    """Create the compiled Supervisor graph (singleton).

    Injects LLM, tool registry, and session repo at construction time.
    The graph is compiled once and reused across all requests.
    """
    return create_supervisor(get_llm(), get_tool_registry(), get_session_repo())
