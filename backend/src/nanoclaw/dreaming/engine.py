"""Dreaming Engine — orchestrates the daily background processing flow.

The engine creates a dedicated Dreaming session, pushes a single subtask
into the TaskQueue, and lets a WorkerPool (with the four Dreaming tools)
execute it.  After the subtask completes, a daily summary is written.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from langchain_core.language_models.chat_models import BaseChatModel

from nanoclaw.models.chat import Session as ChatSession
from nanoclaw.models.task import Subtask, TaskPlan

if TYPE_CHECKING:
    from nanoclaw.eval.logger import EventLogger
    from nanoclaw.memory.store import MemoryStore
    from nanoclaw.storage.session_repo import SessionRepository
    from nanoclaw.storage.task_queue import TaskQueue
    from nanoclaw.tools.registry import ToolRegistry


class DreamingEngine:
    """Background dreaming orchestrator.

    Usage::

        engine = DreamingEngine(
            eval_logger=logger,
            memory_store=store,
            task_queue=queue,
            session_repo=repo,
            llm=chat_model,
            dreaming_tools=registry,
            eval_base_dir=settings.eval_dir,
        )
        summary = await engine.run_dreaming("2026-06-08")
    """

    def __init__(
        self,
        eval_logger: EventLogger,
        memory_store: MemoryStore,
        session_repo: SessionRepository,
        llm: BaseChatModel,
        dreaming_tools: ToolRegistry,
        task_queue: TaskQueue | None = None,
        eval_base_dir: str = "",
    ) -> None:
        self._eval_logger = eval_logger
        self._memory_store = memory_store
        self._task_queue = task_queue
        self._session_repo = session_repo
        self._llm = llm
        self._dreaming_tools = dreaming_tools
        self._eval_base_dir = Path(eval_base_dir) if eval_base_dir else Path(".nanoclaw/eval")

    # ── Public API ───────────────────────────────────────────────

    async def run_dreaming(self, date_str: str | None = None) -> dict[str, Any]:
        """Execute the full dreaming flow for a given date.

        Args:
            date_str: Date string ``YYYY-MM-DD``.  Defaults to today.

        Returns:
            Summary dict with date, subtask results, and timestamp.
        """
        date_str = date_str or datetime.now().strftime("%Y-%m-%d")

        from nanoclaw.agent.nodes.react_agent import create_react_agent
        from nanoclaw.agent.worker_pool import WorkerPool
        from nanoclaw.storage.task_queue import MemoryQueue

        # Create a dedicated Dreaming worker (one subtask, one worker)
        dreaming_agent = create_react_agent(self._llm, self._dreaming_tools)
        pool = WorkerPool(
            task_queue=self._task_queue,
            react_agent=dreaming_agent,
            llm=self._llm,
            num_workers=1,
        )

        # Create session and plan
        session = await self._create_dreaming_session(date_str)
        plan = self._create_dreaming_plan(session.id, date_str)

        # Execute
        tq = self._task_queue if self._task_queue is not None else MemoryQueue()
        await tq.init_plan(plan)
        await pool.start()
        try:
            results = await tq.wait_for_all()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._eval_logger.log_event("dreaming", "error", {
                "date": date_str,
                "message": f"Dreaming failed: {exc}",
            })
            raise
        finally:
            await pool.stop()

        return self._write_daily_summary(date_str, results)

    # ── Internal helpers ─────────────────────────────────────────

    async def _create_dreaming_session(self, date_str: str) -> ChatSession:
        """Create a dedicated session for dreaming (no user messages)."""
        session_id = f"dreaming_{date_str}"
        session = ChatSession(id=session_id, created_at=time.time())
        await self._session_repo.create(session)
        return session

    def _create_dreaming_plan(self, session_id: str, date_str: str) -> TaskPlan:
        """Build a single-subtask plan for the Dreaming Agent."""
        desc = (
            f"Run daily dreaming for {date_str}. "
            "Steps: "
            "1) read evaluation logs with read_eval_logs, "
            "2) analyse patterns with llm_analyze, "
            "3) write discovered skills and user preferences with write_memory, "
            "4) read existing memory with read_memory to avoid duplicates, "
            "5) generate a brief summary."
        )
        subtask = Subtask(
            id="dream_001",
            description=desc,
            tools_needed=[
                "read_eval_logs",
                "write_memory",
                "read_memory",
                "llm_analyze",
            ],
        )
        return TaskPlan(session_id=session_id, subtasks=[subtask])

    def _write_daily_summary(
        self,
        date_str: str,
        results: dict[str, str | None],
    ) -> dict[str, Any]:
        """Write the dreaming summary JSON and return it."""
        summary: dict[str, Any] = {
            "date": date_str,
            "subtasks_completed": len(results),
            "subtasks": {
                tid: (res or "")[:300] if res else None
                for tid, res in results.items()
            },
            "timestamp": time.time(),
        }
        summary_dir = self._eval_base_dir / "daily" / date_str
        summary_dir.mkdir(parents=True, exist_ok=True)
        (summary_dir / "summary.json").write_text(
            json.dumps(summary, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        return summary


# ── Module-level utility ─────────────────────────────────────────


def extract_tool_chains(
    events: list[dict[str, Any]],
    chain_length: int = 2,
) -> dict[tuple[str, ...], int]:
    """Extract consecutive tool-call chains from evaluation events.

    Groups ``tool_call`` events by ``session_id``, then uses a sliding
    window to count how often each tool sequence appears.

    Returns a dict like ``{("read_file", "grep"): 3, ...}``.
    """
    tool_events = [e for e in events if e.get("type") == "tool_call"]

    sessions: dict[str, list[str]] = {}
    for e in tool_events:
        data = e.get("data", {})
        sid = data.get("session_id", "")
        tool = data.get("tool_name", "") or data.get("tool", "")
        if not sid or not tool:
            continue
        sessions.setdefault(sid, []).append(tool)

    chains: dict[tuple[str, ...], int] = {}
    for names in sessions.values():
        for i in range(len(names) - chain_length + 1):
            chain = tuple(names[i : i + chain_length])
            chains[chain] = chains.get(chain, 0) + 1

    return chains
