"""Task queue abstraction and in-memory DAG-aware implementation.

TaskQueue is not a simple FIFO — it understands subtask dependencies
and only yields subtasks whose dependencies are satisfied.
Designed to be shared across Workers for multi-task execution (Phase 2+).
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import asdict

from nanoclaw.models.task import Subtask, TaskPlan, TaskStatus


class TaskQueue(ABC):
    """Abstract DAG-aware task queue interface."""

    @abstractmethod
    async def init_plan(self, plan: TaskPlan) -> None:
        """Load a TaskPlan, parse DAG, enqueue leaf nodes."""

    @abstractmethod
    async def dequeue(self) -> Subtask | None:
        """Return a runnable subtask (dependencies satisfied), or None."""

    @abstractmethod
    async def complete(self, task_id: str, result: str) -> None:
        """Mark a subtask complete; enqueue newly-runnable downstream tasks."""

    @abstractmethod
    async def requeue(self, subtask: Subtask) -> None:
        """Re-enqueue a subtask for retry (after a failed check).

        Resets the subtask status to PENDING so it can be picked up again.
        """

    @abstractmethod
    async def fail(self, task_id: str, error: str) -> None:
        """Mark a subtask failed; mark all downstream as CANCELLED."""

    @abstractmethod
    async def compensate(self, task_id: str, success: bool, error: str = "") -> None:
        """Mark a subtask compensated (status COMPENSATED or COMPENSATION_FAILED);
        cascade cancel downstream. Called after budget-exhausted compensation."""

    @abstractmethod
    async def wait_for_all(self) -> dict[str, str | None]:
        """Block until all subtasks reach a terminal state.

        Returns {task_id: result} for inspection.
        """

    @abstractmethod
    async def snapshot(self) -> dict:
        """Serialize queue state for checkpoint persistence."""

    async def renew_lease(self, task_id: str) -> None:
        """Extend the worker lease for a running subtask (heartbeat).

        No-op in MemoryQueue — only relevant for RedisQueue.
        """
        pass

    @abstractmethod
    async def restore(self, snapshot: dict) -> None:
        """Rebuild queue state from a checkpoint snapshot."""


class MemoryQueue(TaskQueue):
    """In-process DAG-aware task queue.

    All mutation methods (init_plan, complete, fail, requeue) are
    protected by an ``asyncio.Lock`` to prevent race conditions when
    multiple workers operate concurrently.

    Internal structures:
      _dag:    task_id  -> list[depends_on]  (forward dependency)
      _rdag:   dep_id   -> list[downstream]   (reverse, for complete/fail cascade)
      _tasks:  task_id  -> Subtask
      _ready:  asyncio.Queue of runnable subtasks
      _events: task_id  -> asyncio.Event (for wait_for_all)
    """

    def __init__(self) -> None:
        self._dag: dict[str, list[str]] = {}
        self._rdag: dict[str, list[str]] = {}
        self._tasks: dict[str, Subtask] = {}
        self._ready: asyncio.Queue[Subtask] = asyncio.Queue()
        self._events: dict[str, asyncio.Event] = {}
        self._lock = asyncio.Lock()

    async def init_plan(self, plan: TaskPlan) -> None:
        async with self._lock:
            self._dag.clear()
            self._rdag.clear()
            self._tasks.clear()
            self._events.clear()

            for s in plan.subtasks:
                self._dag[s.id] = list(s.depends_on)
                self._tasks[s.id] = s
                for dep in s.depends_on:
                    self._rdag.setdefault(dep, []).append(s.id)

            # Leaf nodes (no dependencies) go straight to ready queue
            for s in plan.subtasks:
                if not s.depends_on:
                    await self._ready.put(s)
                self._events[s.id] = asyncio.Event()

    async def dequeue(self) -> Subtask | None:
        try:
            return await asyncio.wait_for(self._ready.get(), timeout=1.0)
        except asyncio.TimeoutError:
            return None

    async def complete(self, task_id: str, result: str) -> None:
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                msg = f"Task {task_id!r} not found"
                raise ValueError(msg)
            task.result = result
            task.status = TaskStatus.SUCCEEDED
            self._events[task_id].set()

            # Enqueue downstream tasks whose dependencies are all satisfied
            for downstream in self._rdag.get(task_id, []):
                deps = self._dag[downstream]
                if all(
                    self._tasks[d].status == TaskStatus.SUCCEEDED for d in deps
                ):
                    await self._ready.put(self._tasks[downstream])

    async def requeue(self, subtask: Subtask) -> None:
        """Re-enqueue a subtask for retry."""
        async with self._lock:
            task = self._tasks.get(subtask.id)
            if task is None:
                msg = f"Task {subtask.id!r} not found"
                raise ValueError(msg)
            task.status = TaskStatus.PENDING
            task.result = None
            task.error = None
            await self._ready.put(task)

    async def fail(self, task_id: str, error: str) -> None:
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                msg = f"Task {task_id!r} not found"
                raise ValueError(msg)
            task.status = TaskStatus.FAILED
            task.error = error
            self._events[task_id].set()

            # Cancel all downstream tasks
            for downstream in self._rdag.get(task_id, []):
                dt = self._tasks.get(downstream)
                if dt is not None:
                    dt.status = TaskStatus.CANCELLED
                    self._events[downstream].set()

    async def compensate(self, task_id: str, success: bool, error: str = "") -> None:
        """Mark a subtask compensated (COMPENSATED or COMPENSATION_FAILED)
        and cascade cancel all downstream tasks."""
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                msg = f"Task {task_id!r} not found"
                raise ValueError(msg)
            task.status = TaskStatus.COMPENSATED if success else TaskStatus.COMPENSATION_FAILED
            if not success and error:
                task.error = error
            self._events[task_id].set()

            # Cancel all downstream (same as fail())
            for downstream in self._rdag.get(task_id, []):
                dt = self._tasks.get(downstream)
                if dt is not None:
                    dt.status = TaskStatus.CANCELLED
                    self._events[downstream].set()

    async def wait_for_all(self) -> dict[str, str | None]:
        await asyncio.gather(*[e.wait() for e in self._events.values()])
        return {
            tid: self._tasks[tid].result for tid in self._tasks
        }

    async def snapshot(self) -> dict:
        return {
            "dag": dict(self._dag),
            "rdag": dict(self._rdag),
            "tasks": {
                tid: {k: v for k, v in asdict(t).items() if k != "trace"}
                for tid, t in self._tasks.items()
            },
        }

    async def restore(self, snapshot: dict) -> None:
        from nanoclaw.models.task import Subtask as SubtaskModel

        self._dag = dict(snapshot["dag"])
        self._rdag = dict(snapshot["rdag"])
        tasks_raw: dict = snapshot["tasks"]
        self._tasks = {}
        self._events = {}
        self._ready = asyncio.Queue()

        for tid, tdata in tasks_raw.items():
            tdata["status"] = TaskStatus(tdata["status"])
            self._tasks[tid] = SubtaskModel(**tdata)
            self._events[tid] = asyncio.Event()
            if tdata["status"] in (TaskStatus.PENDING, TaskStatus.RETRYING):
                await self._ready.put(self._tasks[tid])
