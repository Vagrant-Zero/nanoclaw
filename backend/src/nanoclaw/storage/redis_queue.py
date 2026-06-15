"""Redis-backed DAG-aware task queue.

Uses Redis data structures for persistence and crash recovery:
  - List (LPUSH/BRPOP) for the ready task queue
  - Hash for per-subtask state (status, result, error, retry_count)
  - Sorted Set (ZSET) for worker leases with expiration timestamps
  - Pub/Sub for wait_for_all() blocking

In-memory DAG mirrors (_dag, _rdag) avoid repeated Redis round-trips
for dependency resolution — the DAG is small and only changes at
init_plan time.
"""

from __future__ import annotations

import json
import asyncio
import time

from redis.asyncio import Redis

from nanoclaw.models.task import Subtask, TaskPlan, TaskStatus
from nanoclaw.storage.redis_client import get_redis
from nanoclaw.storage.task_queue import TaskQueue


class RedisQueue(TaskQueue):
    _LEASE_EXPIRE_SECONDS = 120  # Worker crash detection timeout

    """Redis-backed DAG-aware task queue with lease-based crash recovery.

    Key namespace: ``nanoclaw:queue:{session_id}:*``

    Each instance is bound to a single session_id and should not be
    shared across sessions.
    """

    KEY_PREFIX = "nanoclaw:queue:"

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        # In-memory DAG mirrors (small, only mutated at init_plan)
        self._dag: dict[str, list[str]] = {}
        self._pending_tasks: set[asyncio.Task] = set()
        self._rdag: dict[str, list[str]] = {}
        self._tasks: dict[str, Subtask] = {}
        self._completed_count = 0
        self._total_count = 0

    # ── Redis key helpers ──────────────────────────────────────────

    @property
    def _q(self) -> str:
        """List key: ready queue (LPUSH / BRPOP)."""
        return f"{self.KEY_PREFIX}{self.session_id}:ready"

    @property
    def _pubsub(self) -> str:
        """Pub/sub channel key: signals ALL_DONE."""
        return f"{self.KEY_PREFIX}{self.session_id}:done"

    @property
    def _leases(self) -> str:
        """Sorted Set key: task_id -> lease_expiry_timestamp."""
        return f"{self.KEY_PREFIX}{self.session_id}:leases"

    def _task_key(self, task_id: str) -> str:
        """Hash key: per-subtask state."""
        return f"{self.KEY_PREFIX}{self.session_id}:tasks:{task_id}"

    # ── TaskQueue interface ────────────────────────────────────────

    async def init_plan(self, plan: TaskPlan) -> None:
        redis = await get_redis()
        self._tasks = {s.id: s for s in plan.subtasks}
        self._total_count = len(plan.subtasks)

        # Build in-memory DAG mirrors
        self._dag.clear()
        self._rdag.clear()
        for s in plan.subtasks:
            self._dag[s.id] = list(s.depends_on)
            for dep in s.depends_on:
                self._rdag.setdefault(dep, []).append(s.id)

        # Persist subtask state to Redis hashes
        for s in plan.subtasks:
            await redis.hset(self._task_key(s.id), mapping={
                "status": s.status.value,
                "result": s.result or "",
                "error": s.error or "",
                "retry_count": str(s.retry_count),
            })

        # Clear any stale queue state (idempotency)
        await redis.delete(self._q)
        await redis.delete(self._leases)

        # Enqueue leaf tasks (no dependencies)
        for s in plan.subtasks:
            if not s.depends_on:
                await redis.lpush(self._q, s.id)

    async def dequeue(self) -> Subtask | None:
        redis = await get_redis()
        # BRPOP with 5s timeout — non-blocking poll
        result = await redis.brpop(self._q, timeout=5)
        if result is None:
            return None

        _, task_id = result
        subtask = self._tasks.get(task_id)
        if subtask is None:
            return None

        # Claim lease: ZADD with expiration
        expire_ts = time.time() + self._LEASE_EXPIRE_SECONDS
        await redis.zadd(self._leases, {task_id: expire_ts})

        # Update in-memory and Redis state
        subtask.status = TaskStatus.RUNNING
        await redis.hset(self._task_key(task_id), "status", TaskStatus.RUNNING.value)
        return subtask

    async def renew_lease(self, task_id: str) -> None:
        """Extend the worker lease for a running subtask (heartbeat).

        Call periodically during long-running tasks to prevent the
        lease from expiring and another worker picking up the task.
        """
        redis = await get_redis()
        expire_ts = time.time() + self._LEASE_EXPIRE_SECONDS
        await redis.zadd(self._leases, {task_id: expire_ts})

    async def complete(self, task_id: str, result: str) -> None:
        redis = await get_redis()
        task = self._tasks.get(task_id)
        if task is None:
            msg = f"Task {task_id!r} not found"
            raise ValueError(msg)

        task.status = TaskStatus.SUCCEEDED
        task.result = result
        self._completed_count += 1

        await redis.zrem(self._leases, task_id)
        await redis.hset(self._task_key(task_id), mapping={
            "status": TaskStatus.SUCCEEDED.value,
            "result": result,
        })

        # Enqueue downstream tasks whose dependencies are all satisfied
        for downstream in self._rdag.get(task_id, []):
            deps = self._dag.get(downstream, [])
            if all(
                self._tasks[d].status == TaskStatus.SUCCEEDED
                for d in deps
            ):
                dtask = self._tasks.get(downstream)
                if dtask is not None:
                    dtask.status = TaskStatus.RUNNING  # Will transition to RUNNING on dequeue
                    await redis.lpush(self._q, downstream)

        self._check_all_done(redis)

    async def requeue(self, subtask: Subtask) -> None:
        """Re-enqueue a subtask for retry."""
        redis = await get_redis()
        task = self._tasks.get(subtask.id)
        if task is None:
            msg = f"Task {subtask.id!r} not found"
            raise ValueError(msg)

        task.status = TaskStatus.PENDING
        task.result = None
        task.error = None
        task.retry_count = subtask.retry_count

        await redis.zrem(self._leases, subtask.id)
        await redis.hset(self._task_key(subtask.id), mapping={
            "status": TaskStatus.PENDING.value,
            "result": "",
            "error": "",
            "retry_count": str(subtask.retry_count),
        })
        await redis.lpush(self._q, subtask.id)

    async def fail(self, task_id: str, error: str) -> None:
        redis = await get_redis()
        task = self._tasks.get(task_id)
        if task is None:
            msg = f"Task {task_id!r} not found"
            raise ValueError(msg)

        task.status = TaskStatus.FAILED
        task.error = error
        self._completed_count += 1

        await redis.zrem(self._leases, task_id)
        await redis.hset(self._task_key(task_id), mapping={
            "status": TaskStatus.FAILED.value,
            "error": error,
        })

        # Cascade cancel: mark all transitive downstream as CANCELLED
        await self._cascade_cancel(task_id)
        self._check_all_done(redis)

    async def compensate(self, task_id: str, success: bool, error: str = "") -> None:
        """Mark a subtask compensated (COMPENSATED or COMPENSATION_FAILED)
        and cascade cancel all downstream."""
        redis = await get_redis()
        task = self._tasks.get(task_id)
        if task is None:
            msg = f"Task {task_id!r} not found"
            raise ValueError(msg)

        status = TaskStatus.COMPENSATED if success else TaskStatus.COMPENSATION_FAILED
        task.status = status
        if not success and error:
            task.error = error
        self._completed_count += 1

        await redis.zrem(self._leases, task_id)
        await redis.hset(self._task_key(task_id), mapping={
            "status": status.value,
            "error": error,
        })

        # Cancel all downstream (same as fail path)
        await self._cascade_cancel(task_id)
        self._check_all_done(redis)

    async def wait_for_all(self, timeout: float = 300.0) -> dict[str, str | None]:
        """Block until all subtasks reach a terminal state.

        Subscribes to a Redis pub/sub channel and waits for the ALL_DONE
        signal. If all tasks are already done, returns immediately.

        Raises asyncio.TimeoutError if *timeout* seconds elapse before
        all tasks complete.
        """
        redis = await get_redis()
        pubsub = redis.pubsub()
        await pubsub.subscribe(self._pubsub)

        # Short-circuit: already done? (checked before AND after subscribe
        # to close the race window with _check_all_done)
        if self._completed_count >= self._total_count:
            await pubsub.unsubscribe(self._pubsub)
            return self._collect_results()

        # Wait for the ALL_DONE message with timeout
        try:
            async with asyncio.timeout(timeout):
                async for message in pubsub.listen():
                    if message["type"] == "message" and message["data"] == "ALL_DONE":
                        break
        except asyncio.TimeoutError:
            await pubsub.unsubscribe(self._pubsub)
            raise

        await pubsub.unsubscribe(self._pubsub)
        return self._collect_results()

    async def snapshot(self) -> dict:
        redis = await get_redis()
        task_snapshots: dict[str, dict[str, str]] = {}
        for task_id in self._tasks:
            data = await redis.hgetall(self._task_key(task_id))
            task_snapshots[task_id] = data

        return {
            "session_id": self.session_id,
            "dag": dict(self._dag),
            "rdag": dict(self._rdag),
            "tasks": {tid: t.to_dict() for tid, t in self._tasks.items()},
            "redis_tasks": task_snapshots,
            "completed_count": self._completed_count,
            "total_count": self._total_count,
        }

    async def restore(self, snapshot: dict) -> None:
        """Rebuild queue state from a checkpoint snapshot.

        Handles:
          - Re-enqueuing PENDING tasks that are ready (no dependencies)
          - Re-enqueuing RETRYING tasks
          - Detecting expired leases and resetting to PENDING
        """
        redis = await get_redis()
        self._dag = dict(snapshot["dag"])
        self._rdag = dict(snapshot["rdag"])
        self._completed_count = snapshot["completed_count"]
        self._total_count = snapshot["total_count"]

        # Clear stale queue data (idempotency)
        await redis.delete(self._q)
        await redis.delete(self._leases)

        # Rehydrate task objects from snapshot
        self._tasks = {}
        for task_id, data in snapshot["tasks"].items():
            subtask = Subtask.from_dict(data)
            self._tasks[task_id] = subtask

            # Re-enqueue PENDING tasks that are ready (no dependencies)
            if subtask.status == TaskStatus.PENDING and not subtask.depends_on:
                await redis.lpush(self._q, task_id)

            # Re-enqueue RETRYING tasks
            if subtask.status == TaskStatus.RETRYING:
                await redis.lpush(self._q, task_id)

            # Check for expired leases and reset to PENDING
            lease_ts = await redis.zscore(self._leases, task_id)
            if lease_ts is not None and lease_ts < time.time():
                subtask.status = TaskStatus.PENDING
                await redis.lpush(self._q, task_id)
                await redis.zrem(self._leases, task_id)

    # ── Internal helpers ───────────────────────────────────────────

    async def _cascade_cancel(self, task_id: str) -> None:
        """Iteratively (BFS) mark all transitive downstream tasks as CANCELLED.

        Uses a stack instead of recursion to avoid stack overflow on
        deep DAG chains (Python default recursion limit: ~1000).
        """
        redis = await get_redis()
        stack = list(self._rdag.get(task_id, []))
        visited: set[str] = set()
        while stack:
            downstream = stack.pop()
            if downstream in visited:
                continue
            visited.add(downstream)
            dt = self._tasks.get(downstream)
            if dt is not None and dt.status == TaskStatus.PENDING:
                dt.status = TaskStatus.CANCELLED
                self._completed_count += 1
                await redis.hset(self._task_key(downstream), "status", TaskStatus.CANCELLED.value)
                # Push transitive downstream tasks
                for child in self._rdag.get(downstream, []):
                    if child not in visited:
                        stack.append(child)

    def _check_all_done(self, redis: Redis) -> None:
        """Publish ALL_DONE if all tasks have reached a terminal state."""
        if self._completed_count >= self._total_count:
            task = asyncio.create_task(redis.publish(self._pubsub, "ALL_DONE"))
            self._pending_tasks.add(task)
            task.add_done_callback(self._pending_tasks.discard)

    def _collect_results(self) -> dict[str, str | None]:
        """Build {task_id: result} from in-memory task states."""
        return {tid: t.result for tid, t in self._tasks.items()}
