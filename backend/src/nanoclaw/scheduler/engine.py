"""Scheduler daemon — periodic task dispatch and Dreaming trigger.

The ``Scheduler`` runs a background loop that checks for due scheduled
tasks every 60 seconds and dispatches them to a dedicated WorkerPool.
A ``DreamingCronTrigger`` checks each cycle whether it is time to run
the daily dreaming process (default: 02:00).
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING
from langchain_core.language_models.chat_models import BaseChatModel

from nanoclaw.models.chat import Session as ChatSession
from nanoclaw.models.task import Subtask, TaskPlan

if TYPE_CHECKING:
    from nanoclaw.dreaming.engine import DreamingEngine
    from nanoclaw.eval.logger import EventLogger
    from nanoclaw.scheduler.repo import ScheduledTask, ScheduledTaskRepo
    from nanoclaw.storage.session_repo import SessionRepository
    from nanoclaw.tools.registry import ToolRegistry

# ── Constants ─────────────────────────────────────────────────────

_SCHEDULER_POLL_SECONDS = 60
_DREAMING_HOUR = 2  # 02:00
_DISPATCH_TIMEOUT = 120  # Max seconds for one dispatch cycle

# ── Scheduler ──────────────────────────────────────────────────────

class Scheduler:
    """Background daemon that dispatches due scheduled tasks and dreaming.

    Usage::

        scheduler = Scheduler(task_repo, session_repo, eval_logger, llm, registry)
        await scheduler.start()
        ...
        await scheduler.stop()
    """

    def __init__(
        self,
        task_repo: ScheduledTaskRepo,
        session_repo: SessionRepository,
        eval_logger: EventLogger,
        llm: BaseChatModel,
        tool_registry: ToolRegistry,
        dreaming_engine: DreamingEngine | None = None,
        dreams_dir: str = ".nanoclaw/dreams",
    ) -> None:
        self._task_repo = task_repo
        self._session_repo = session_repo
        self._eval_logger = eval_logger
        self._llm = llm
        self._tool_registry = tool_registry
        self._dreaming_engine = dreaming_engine

        self._running = False
        self._loop_task: asyncio.Task | None = None
        self._running_tasks: set[str] = set()
        self._dreaming_triggered_today: str = ""
        self._dreams_dir = Path(dreams_dir)

    @property
    def task_repo(self) -> ScheduledTaskRepo:
        """Expose the task repo for API endpoints."""
        return self._task_repo

    # ── Lifecycle ──

    async def start(self) -> None:
        """Start the background polling loop."""
        if self._running:
            return
        # Restore dreaming trigger state from file (survives restart)
        state_path = self._dreams_dir / ".last_dreaming"
        if state_path.exists():
            self._dreaming_triggered_today = state_path.read_text().strip()
        self._running = True
        self._loop_task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        """Stop the background polling loop."""
        self._running = False
        if self._loop_task is not None:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
            self._loop_task = None

    # ── Main loop ──

    async def _run_loop(self) -> None:
        """Main loop: check tasks and dreaming every 60 seconds."""
        while self._running:
            try:
                async with asyncio.timeout(_DISPATCH_TIMEOUT):
                    await self._check_and_dispatch()
            except asyncio.TimeoutError:
                logger.warning("Scheduler dispatch cycle timed out (>{_DISPATCH_TIMEOUT}s) — continuing")
            except Exception:
                logger.warning('Scheduler loop error', exc_info=True)  # Non-fatal — loop keeps running
            await asyncio.sleep(_SCHEDULER_POLL_SECONDS)

    async def _check_and_dispatch(self) -> None:
        """Check for due tasks and trigger dreaming if applicable."""
        # Dispatch due scheduled tasks (skip already-running)
        due = await self._task_repo.get_due_tasks()
        for task in due:
            if task.id in self._running_tasks:
                continue
            await self._dispatch_task(task)

        # Trigger dreaming
        await self._check_dreaming()

    # ── Task dispatch ──

    async def _dispatch_task(self, task: ScheduledTask) -> None:
        """Execute a single scheduled task with its own WorkerPool."""
        from nanoclaw.agent.nodes.react_agent import create_react_agent
        from nanoclaw.agent.worker_pool import WorkerPool
        from nanoclaw.storage.task_queue import MemoryQueue
        self._running_tasks.add(task.id)
        try:
            task_queue = MemoryQueue()
            agent = create_react_agent(self._llm, self._tool_registry)
            pool = WorkerPool(
                task_queue=task_queue,
                react_agent=agent,
                llm=self._llm,
                num_workers=1,
            )

            now = datetime.now()
            session_id = (
                f"sched_{task.id}_{now.strftime('%Y%m%d_%H%M%S')}"
            )
            session = ChatSession(id=session_id, created_at=time.time())
            await self._session_repo.create(session)

            subtask = Subtask(
                id=task.id,
                description=task.prompt or task.description,
            )
            plan = TaskPlan(session_id=session_id, subtasks=[subtask])

            await self._eval_logger.log_event(
                session_id, "task_start",
                {"task_id": task.id, "description": task.description,
                 "subtask_count": 1, "created_at": time.time()},
            )

            await task_queue.init_plan(plan)
            await pool.start()
            try:
                await asyncio.wait_for(
                    task_queue.wait_for_all(), timeout=300,
                )
            except asyncio.TimeoutError:
                pass  # Task exceeded deadline; continue
            finally:
                await pool.stop()

            # Update last_run
            await self._task_repo.update_last_run(
                task.id, now.isoformat(),
            )
        finally:
            self._running_tasks.discard(task.id)

    # ── Dreaming trigger ──

    async def _check_dreaming(self) -> None:
        """Trigger daily dreaming if it is past 02:00 and not yet done."""
        if self._dreaming_engine is None:
            return

        now = datetime.now()
        today = now.strftime("%Y-%m-%d")

        if self._dreaming_triggered_today == today:
            return  # Already triggered

        if now.hour < _DREAMING_HOUR:
            return  # Not yet time

        self._dreaming_triggered_today = today
        # Persist to file so the state survives a restart
        state_path = self._dreams_dir / ".last_dreaming"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(today)
        yesterday = (now - __import__("datetime").timedelta(days=1)).strftime(
            "%Y-%m-%d"
        )
        await self._dreaming_engine.run_dreaming(yesterday)
