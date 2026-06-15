"""Dreaming Agent tool set — four tools for background data mining.

Each tool follows the ``BaseTool`` protocol so it can be registered in a
``ToolRegistry`` and used by the Dreaming Agent ReAct loop.
"""

from __future__ import annotations

import asyncio
import json
from langchain_core.language_models.chat_models import BaseChatModel
from pathlib import Path
from typing import TYPE_CHECKING, Any

from nanoclaw.memory.types import MemoryEntry, MemoryType
from nanoclaw.tools.base import BaseTool, ToolSpec

if TYPE_CHECKING:
    from nanoclaw.memory.store import MemoryStore


# ── Tool: read_eval_logs ───────────────────────────────────────────


class ReadEvalLogsTool(BaseTool):
    """Read evaluation logs for a given date or session.

    Filters by event type, session ID, or date.  Returns JSON.
    """

    spec = ToolSpec(
        name="read_eval_logs",
        description=(
            "Read evaluation logs for a given date range or session. "
            "Filters by event type, session ID, or keyword."
        ),
        parameters={
            "type": "object",
            "properties": {
                "date": {
                    "type": "string",
                    "description": "Date string YYYY-MM-DD",
                },
                "session_id": {
                    "type": "string",
                    "description": "Specific session ID",
                },
                "event_type": {
                    "type": "string",
                    "description": "Filter: task_start, task_end, tool_call, etc.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max events to return (default 100)",
                },
            },
            "required": [],
        },
    )

    def __init__(self, eval_base_dir: str) -> None:
        self._base = Path(eval_base_dir)

    def run(  # type: ignore[override]
        self,
        date: str = "",
        session_id: str = "",
        event_type: str = "",
        limit: int = 100,
    ) -> str:
        sessions: list[str] = []
        if session_id:
            sessions = [session_id]
        else:
            if self._base.exists():
                sessions = sorted(
                    d.name for d in self._base.iterdir() if d.is_dir()
                )
            if date:
                day_prefix = date.replace("-", "")
                sessions = [s for s in sessions if s.startswith(day_prefix)]

        events: list[dict] = []
        for sid in sessions:
            path = self._base / sid / "events.jsonl"
            if not path.exists():
                continue
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    ev = json.loads(line)
                    if event_type and ev.get("type") != event_type:
                        continue
                    events.append(ev)
                    if len(events) >= limit:
                        break
            if len(events) >= limit:
                break

        return json.dumps(events, ensure_ascii=False, default=str)[:5000]


# ── Tool: write_memory ──────────────────────────────────────────────



def _resolve_memtype(val: str) -> MemoryType:
    """Convert a string to MemoryType, falling back to REFLECTION."""
    try:
        return MemoryType(val)
    except ValueError:
        return MemoryType.REFLECTION



class WriteMemoryTool(BaseTool):
    """Write an entry to the long-term memory store."""

    spec = ToolSpec(
        name="write_memory",
        description=(
            "Write an entry to the long-term memory store. "
            "Used to persist skills, user profile data, or reflections."
        ),
        parameters={
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "enum": ["skill", "user_profile", "semantic", "reflection"],
                    "description": "Memory entry type",
                },
                "content": {
                    "type": "string",
                    "description": "The memory content",
                },
                "tags": {
                    "type": "string",
                    "description": "Comma-separated tags for retrieval",
                },
                "confidence": {
                    "type": "number",
                    "description": "Confidence score 0.0-1.0",
                },
            },
            "required": ["type", "content"],
        },
    )

    def __init__(self, memory_store: MemoryStore) -> None:
        self._store = memory_store

    def run(  # type: ignore[override]
        self,
        type: str = "reflection",
        content: str = "",
        tags: str = "",
        confidence: float = 0.5,
    ) -> str:
        entry = MemoryEntry(
            type=_resolve_memtype(type),
            tags=[t.strip() for t in tags.split(",") if t.strip()],
            content=content,
            confidence=min(max(confidence, 0.0), 1.0),
            confirmed=True,
        )
        asyncio.run(self._store.save(entry))
        return f"Saved memory entry {entry.id[:8]} ({type})"


# ── Tool: read_memory ──────────────────────────────────────────────


class ReadMemoryTool(BaseTool):
    """Search existing memory entries."""

    spec = ToolSpec(
        name="read_memory",
        description=(
            "Search existing memory entries. "
            "Used to avoid duplicate skill/profile entries."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query",
                },
                "type_filter": {
                    "type": "string",
                    "enum": ["", "skill", "user_profile", "semantic", "reflection"],
                    "description": "Filter by memory type",
                },
                "tags": {
                    "type": "string",
                    "description": "Comma-separated tags",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Max results (default 5)",
                },
            },
            "required": ["query"],
        },
    )

    def __init__(self, memory_store: MemoryStore) -> None:
        self._store = memory_store

    def run(  # type: ignore[override]
        self,
        query: str = "",
        type_filter: str = "",
        tags: str = "",
        top_k: int = 5,
    ) -> str:
        tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None
        results = asyncio.run(self._store.search(query, tags=tag_list, top_k=top_k))
        if type_filter:
            results = [r for r in results if r.type.value == type_filter]
        return json.dumps(
            [
                {
                    "id": r.id[:8],
                    "type": r.type.value,
                    "content": r.content[:200],
                    "tags": r.tags,
                    "confidence": r.confidence,
                    "confirmed": r.confirmed,
                }
                for r in results
            ],
            ensure_ascii=False,
        )


# ── Tool: llm_analyze ──────────────────────────────────────────────


class LlmAnalyzeTool(BaseTool):
    """Analyze a dataset using an LLM (insight extraction, pattern detection)."""

    spec = ToolSpec(
        name="llm_analyze",
        description=(
            "Analyze a dataset using an LLM. "
            "Used for pattern detection, summarisation, and insight extraction."
        ),
        parameters={
            "type": "object",
            "properties": {
                "data": {
                    "type": "string",
                    "description": "The data to analyse",
                },
                "instruction": {
                    "type": "string",
                    "description": "What to look for or analyse",
                },
            },
            "required": ["data", "instruction"],
        },
    )

    def __init__(self, llm: BaseChatModel) -> None:
        self._llm = llm

    def run(  # type: ignore[override]
        self,
        data: str = "",
        instruction: str = "",
    ) -> str:
        from langchain_core.messages import HumanMessage, SystemMessage

        prompt = SystemMessage(
            content=(
                "You are a data analysis engine. Analyse the provided data "
                "according to the instruction and return your findings.\n\n"
                f"Instruction: {instruction}"
            )
        )
        response = asyncio.run(
            self._llm.ainvoke([prompt, HumanMessage(content=data[:8000])])
        )
        return str(response.content)[:4000]


# ── Registry factory ────────────────────────────────────────────────


def register_dreaming_tools(
    registry: Any,
    eval_base_dir: str,
    memory_store: MemoryStore,
    llm: BaseChatModel,
) -> None:
    """Register all four Dreaming tools on an existing ``ToolRegistry``."""
    from nanoclaw.tools.registry import ToolRegistry

    if not isinstance(registry, ToolRegistry):
        msg = "registry must be a ToolRegistry instance"
        raise TypeError(msg)

    registry.register(ReadEvalLogsTool(eval_base_dir))
    registry.register(WriteMemoryTool(memory_store))
    registry.register(ReadMemoryTool(memory_store))
    registry.register(LlmAnalyzeTool(llm))
