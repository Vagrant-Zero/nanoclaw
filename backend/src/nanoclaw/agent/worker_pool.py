"""Worker pool — manages N async workers pulling from TaskQueue.

Stub for Phase 2, Task 6.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from nanoclaw.storage.task_queue import TaskQueue


class WorkerPool:
    """Manages a pool of async workers.

    Each worker pulls subtasks from the TaskQueue and executes
    them with the ReAct agent.
    """

    def __init__(
        self,
        task_queue: Any,
        react_agent: Any,
        num_workers: int = 3,
        sse_callback: Callable | None = None,
    ) -> None:
        self._task_queue = task_queue
        self._react_agent = react_agent
        self._num_workers = num_workers
        self._sse_callback = sse_callback
        self._workers: list[Any] = []
        self._running = False

    async def start(self) -> None:
        """Start worker pool."""
        self._running = True
        # Stub

    async def stop(self) -> None:
        """Stop all workers."""
        self._running = False
        # Stub
