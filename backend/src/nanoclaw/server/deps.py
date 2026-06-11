"""FastAPI dependency injection helpers.

Provides singleton instances of LLM, supervisor graph, and storage
repositories. These are created once at application startup and shared
across all requests — not reconstructed per-request.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from langchain_openai import ChatOpenAI

from nanoclaw.agent.supervisor_graph import create_supervisor
from nanoclaw.config import settings
from nanoclaw.storage.session_repo import MemorySessionRepo, SessionRepository
from nanoclaw.tools.registry import ToolRegistry


@lru_cache
def get_llm() -> Any:
    """Create the LangChain chat model (singleton).

    Uses langchain-openai with DeepSeek-compatible base URL.
    The model instance is cached across requests.
    """
    return ChatOpenAI(
        model=settings.llm_model,
        api_key=settings.openai_api_key or "sk-not-configured",
        base_url="https://api.deepseek.com",
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
def get_session_repo() -> SessionRepository:
    """Create the session repository (singleton).

    Phase 1: MemorySessionRepo (in-process dict).
    Phase 5+: swap to PgSessionRepo.
    """
    return MemorySessionRepo()


@lru_cache
def get_supervisor() -> Any:
    """Create the compiled Supervisor graph (singleton).

    Injects LLM, tool registry, and session repo at construction time.
    The graph is compiled once and reused across all requests.
    """
    return create_supervisor(get_llm(), get_tool_registry(), get_session_repo())
