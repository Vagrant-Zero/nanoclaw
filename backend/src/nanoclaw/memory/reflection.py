"""ReflectionEngine — post-task experience extraction and memory creation.

After a task completes, the engine analyses execution traces via LLM,
extracts skills, user preferences, and insights, and saves them as
unconfirmed MemoryEntries for user approval.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Callable
from langchain_core.language_models.chat_models import BaseChatModel

from langchain_core.messages import HumanMessage, SystemMessage

from nanoclaw.memory.types import MemoryEntry, MemoryType

if TYPE_CHECKING:
    from nanoclaw.memory.store import MemoryStore
_REFLECTION_SYSTEM_PROMPT = """You are a reflection engine. Analyse the task execution trace below and extract structured learnings.

Return a JSON object with an "entries" array. Each entry has:
- "type": "skill" | "user_profile" | "reflection"
- "content": concise description
- "tags": list of relevant keywords (e.g. ["python", "file_ops"])
- "confidence": float 0.0-1.0 (how confident you are this is useful)

Rules:
- Skills: reusable tool patterns or workflows observed (0-3 entries)
- User profiles: user preferences, language choices, habits (0-2)
- Reflections: general insights, lessons learned (0-2)
- Be concise — each entry under 200 chars.
- Only extract what's genuinely observable from the trace."""

class ReflectionEngine:
    """Generates experience entries from completed task executions.

    Usage::

        engine = ReflectionEngine(memory_store, llm=chat_model)
        asyncio.create_task(
            engine.reflect(session_id, plan.subtasks, worker_results)
        )
    """

    def __init__(
        self,
        memory_store: MemoryStore,
        llm: BaseChatModel | None = None,
    ) -> None:
        self._store = memory_store
        self._llm = llm

    async def reflect(
        self,
        session_id: str,
        subtasks: list,
        task_results: dict[str, str | None],
        emit_event: Callable | None = None,
    ) -> None:
        """Analyse task execution and save experience entries.

        Args:
            session_id: The session that ran the tasks.
            subtasks: List of Subtask objects (from TaskPlan).
            task_results: ``{subtask_id: result_string}`` from the collect step.
            emit_event: Optional async callback for ``experience_ready`` events.
        """
        if self._llm is None:
            return

        prompt = self._build_prompt(subtasks, task_results)
        try:
            response = await self._llm.ainvoke(
                [SystemMessage(content=_REFLECTION_SYSTEM_PROMPT),
                 HumanMessage(content=prompt)],
                response_format={"type": "json_object"},
            )
        except Exception:
            return  # Reflection failure is non-critical

        entries = self._parse_entries(response.content, session_id)
        for entry in entries:
            try:
                await self._store.save(entry)
                if emit_event is not None:
                    await emit_event("experience_ready", {
                        "entry_id": entry.id,
                        "summary": entry.content[:200],
                        "type": entry.type.value,
                    })
            except Exception:
                logger.warning('Reflection subtask error', exc_info=True)  # Per-entry failure should not cascade

    # ── Internal ──

    def _build_prompt(
        self,
        subtasks: list,
        task_results: dict[str, str | None],
    ) -> str:
        lines: list[str] = ["## Task Execution Trace\n"]
        for s in subtasks:
            sid = getattr(s, "id", "?")
            desc = getattr(s, "description", "")
            status_v = getattr(s, "status", None)
            status = status_v.value if status_v else "UNKNOWN"
            result = (task_results.get(sid) or "")[:200]
            lines.append(f"Subtask {sid}: {desc}")
            lines.append(f"  Status: {status}")
            if result:
                lines.append(f"  Result: {result}")
            lines.append("")
        return "\n".join(lines)

    def _parse_entries(
        self,
        text: str,
        session_id: str,
    ) -> list[MemoryEntry]:
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return []

        raw_entries = data.get("entries", []) if isinstance(data, dict) else []
        entries: list[MemoryEntry] = []

        for raw in raw_entries:
            entry_type = raw.get("type", "reflection")
            try:
                mem_type = MemoryType(entry_type)
            except ValueError:
                mem_type = MemoryType.REFLECTION

            entries.append(MemoryEntry(
                type=mem_type,
                tags=raw.get("tags", []),
                content=raw.get("content", ""),
                source=session_id,
                confidence=float(raw.get("confidence", 0.5)),
                confirmed=False,
            ))

        return entries
