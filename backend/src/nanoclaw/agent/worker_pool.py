"""Worker pool — manages N async workers executing subtasks from the TaskQueue.

Each worker:
1. Dequeues a subtask from the DAG-aware TaskQueue
2. Marks it RUNNING and emits an SSE event
3. Executes via the ReAct subgraph (with 5-minute timeout)
4. Checks the result against the subtask's Rubric
5. On PASS: marks the subtask COMPLETE, emits SUCCEEDED
6. On FAIL:
   - "execution" category → requeue for retry (budget permitting)
   - "planning" category → mark FAILED (collector handles re-plan)
   - Budget exhausted → emit iteration_exhausted
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Callable

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langgraph.graph.state import CompiledStateGraph

from nanoclaw.agent.checker.checker import Checker
from nanoclaw.agent.checker.iteration_budget import IterationBudget
from nanoclaw.models.task import Subtask, TaskStatus

import logging
logger = logging.getLogger(__name__)

# Compensation: exit code constants
_COMPENSATION_OK = 0

if TYPE_CHECKING:
    from nanoclaw.storage.session_repo import SessionRepository
    from nanoclaw.storage.task_queue import TaskQueue
    from nanoclaw.tools.registry import ToolRegistry

_WORKER_TIMEOUT_SECONDS = 300  # 5 minutes max per subtask
_DEQUEUE_POLL_SECONDS = 0.1

class WorkerPool:
    """Manages a pool of concurrent async workers.

    Usage::

        pool = WorkerPool(
            task_queue=memory_queue,
            react_agent=create_react_agent(llm, registry),
            llm=llm,
            num_workers=3,
        )
        await pool.start()
        ...
        await pool.stop()
    """

    def __init__(
        self,
        task_queue: TaskQueue,
        react_agent: CompiledStateGraph,
        num_workers: int = 3,
        llm: BaseChatModel | None = None,
        sse_callback: Callable | None = None,
    ) -> None:
        self._task_queue = task_queue
        self._react_agent = react_agent
        self._num_workers = num_workers
        self._sse_callback = sse_callback or _null_sse_callback

        self._workers: list[asyncio.Task] = []
        self._running = False
        self._checker = Checker(llm=llm)
        self._iteration_budget = IterationBudget()

        # Context injected before start()
        self.session_id: str = ""
        self.session_repo: SessionRepository | None = None

    # ── Lifecycle ──

    async def start(self) -> None:
        """Create and launch *num_workers* background tasks."""
        assert self._react_agent is not None, (
            "react_agent must be set before start()"
        )
        assert self._task_queue is not None, (
            "task_queue must be set before start()"
        )
        if self._running:
            return
        self._running = True
        self._workers = [
            asyncio.create_task(
                self._worker_loop(i), name=f"worker-{i}"
            )
            for i in range(self._num_workers)
        ]

    async def stop(self) -> None:
        """Signal all workers to stop and wait for their completion."""
        self._running = False
        for w in self._workers:
            w.cancel()
        if self._workers:
            await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()

    # ── Worker loop ──

    async def _worker_loop(self, worker_id: int) -> None:
        """Main loop: dequeue → execute → check → complete/retry/fail."""
        while self._running:
            subtask: Subtask | None
            try:
                subtask = await self._task_queue.dequeue()
            except Exception:
                logger.warning('Worker dequeue error', exc_info=True)
                await asyncio.sleep(_DEQUEUE_POLL_SECONDS)
                continue

            if subtask is None:
                await asyncio.sleep(_DEQUEUE_POLL_SECONDS)
                continue

            # Mark running
            subtask.status = TaskStatus.RUNNING
            await self._emit("task_status", {
                "task_id": subtask.id,
                "status": "RUNNING",
            })

            try:
                # Start heartbeat lease renewal; cancelled after execution
                _lease_task = asyncio.create_task(
                    _heartbeat_lease(self._task_queue, subtask.id)
                )
                # Execute with timeout
                result = await asyncio.wait_for(
                    self._react_agent.ainvoke({
                        "messages": [
                            HumanMessage(content=subtask.description)
                        ],
                        "session_id": self.session_id,
                        "task_id": subtask.id,
                        "session_repo": self.session_repo,
                    }),
                    timeout=_WORKER_TIMEOUT_SECONDS,
                )

                # Guard against None return from ainvoke
                if result is None:
                    result_content = ""
                else:
                    messages = result.get("messages", [])
                    result_content = messages[-1].content if messages else ""

                # Check result
                # Stop lease renewal heartbeat
                _lease_task.cancel()

                check_result = await self._checker.check(
                    subtask, result_content,
                    user_request=self._state_user_request(),
                )

                if check_result.passed:
                    await self._on_passed(subtask, result_content)
                else:
                    await self._on_failed(subtask, result_content, check_result)

            except asyncio.TimeoutError:
                _lease_task.cancel()
                await self._on_timeout(subtask)
            except Exception as exc:
                _lease_task.cancel()
                await self._on_error(subtask, exc)

    # ── Result handlers ──

    async def _on_passed(self, subtask: Subtask, result: str) -> None:
        """Subtask passed check — mark complete and emit events."""
        await self._task_queue.complete(subtask.id, result)
        await self._emit("task_status", {
            "task_id": subtask.id,
            "status": "SUCCEEDED",
        })
        await self._emit("check_result", {
            "task_id": subtask.id,
            "passed": True,
            "feedback": "All checks passed",
            "failure_category": None,
        })

    async def _on_failed(
        self, subtask: Subtask, result: str, check_result: CheckResult,
    ) -> None:
        """Subtask failed check — classify and decide next step."""
        category = check_result.failure_category or "execution"

        await self._emit("check_result", {
            "task_id": subtask.id,
            "passed": False,
            "feedback": check_result.feedback,
            "failure_category": category,
        })

        if category == "execution":
            await self._handle_execution_failure(subtask)
        else:
            await self._handle_planning_failure(subtask)

    async def _handle_execution_failure(self, subtask: Subtask) -> None:
        """Retry execution failures (budget permitting).

        When retries are exhausted, attempt cascading rollback compensation
        if the subtask has a compensation command defined.
        """
        # Check budget first, then increment — avoids inflated count in
        # the error message when budget is already exhausted.
        budget_ok = await self._iteration_budget.try_consume(subtask.id)

        if budget_ok:
            subtask.retry_count += 1
            await self._emit("task_status", {
                "task_id": subtask.id,
                "status": "RETRYING",
            })
            subtask.status = TaskStatus.RETRYING
            await self._task_queue.requeue(subtask)
        else:
            await self._emit("iteration_exhausted", {
                "session_id": self.session_id,
                "failed_subtask_ids": [subtask.id],
                "trajectory_paths": [],
                "budget": {
                    "global_count": self._iteration_budget.state.global_count,
                    "global_max": self._iteration_budget.global_max,
                    "per_subtask": self._iteration_budget.state.per_subtask,
                },
            })

            # Attempt cascading rollback compensation
            compensated = await self._run_compensation(subtask)
            if compensated:
                await self._task_queue.compensate(subtask.id, success=True)
            else:
                await self._task_queue.compensate(
                    subtask.id,
                    success=False,
                    error=f"Iteration budget exhausted (retried {subtask.retry_count}x)",
                )

    async def _handle_planning_failure(self, subtask: Subtask) -> None:
        """Planning failures → mark FAILED; collector decides next step."""
        await self._task_queue.fail(
            subtask.id,
            f"Planning failure: check did not pass on subtask {subtask.id}",
        )

    async def _on_timeout(self, subtask: Subtask) -> None:
        """Execution timeout → treat as planning failure."""
        await self._emit("task_status", {
            "task_id": subtask.id,
            "status": "FAILED",
        })
        await self._task_queue.fail(
            subtask.id,
            f"Execution timed out after {_WORKER_TIMEOUT_SECONDS}s",
        )

    async def _run_compensation(self, subtask: Subtask) -> bool:
        """Execute a subtask's compensation command for cascading rollback.

        Returns True if compensation succeeded (COMPENSATED), False if
        it failed or no compensation command is defined (COMPENSATION_FAILED).
        """
        cmd = subtask.compensation
        if not cmd:
            return False  # No compensation defined — simple fail

        await self._emit("task_status", {
            "task_id": subtask.id, "status": "COMPENSATING",
        })
        subtask.status = TaskStatus.COMPENSATING

        try:
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=30.0,
            )
            if proc.returncode == _COMPENSATION_OK:
                subtask.status = TaskStatus.COMPENSATED
                await self._emit("task_status", {
                    "task_id": subtask.id, "status": "COMPENSATED",
                })
                return True

            error_msg = stderr.decode().strip() or f"exit code {proc.returncode}"
            subtask.status = TaskStatus.COMPENSATION_FAILED
            subtask.error = f"Compensation failed: {error_msg}"
            await self._emit("task_status", {
                "task_id": subtask.id, "status": "COMPENSATION_FAILED",
                "error": subtask.error,
            })
            return False
        except asyncio.TimeoutError:
            subtask.status = TaskStatus.COMPENSATION_FAILED
            subtask.error = "Compensation timed out after 30s"
            await self._emit("task_status", {
                "task_id": subtask.id, "status": "COMPENSATION_FAILED",
                "error": subtask.error,
            })
            return False
        except Exception as exc:
            subtask.status = TaskStatus.COMPENSATION_FAILED
            subtask.error = f"Compensation error: {exc}"
            await self._emit("task_status", {
                "task_id": subtask.id, "status": "COMPENSATION_FAILED",
                "error": subtask.error,
            })
            return False

    async def _on_error(self, subtask: Subtask, exc: Exception) -> None:
        """Unexpected error during execution."""
        await self._emit("task_status", {
            "task_id": subtask.id,
            "status": "FAILED",
        })
        await self._task_queue.fail(subtask.id, f"Worker error: {exc}")

    # ── Helpers ──

    async def emit_event(self, event: str, data: dict) -> None:
        """Emit an SSE event through the pool's callback (public API).

        Used by the dispatch node to emit ``agent_plan`` and other
        graph-level events before workers start.
        """
        await self._emit(event, data)

    async def _emit(self, event: str, data: dict) -> None:
        """Emit an SSE event (non-fatal on failure)."""
        try:
            if callable(self._sse_callback):
                await self._sse_callback(event, data)
        except Exception:
            logger.warning(f"Exception in {fpath.name}: %s", _exc_info=True)

    def _state_user_request(self) -> str:
        """Build a generic user request string from session context."""
        return f"Execute subtasks for session {self.session_id}"

async def _null_sse_callback(event: str, data: dict) -> None:
    """Default no-op SSE callback."""
    pass


async def _heartbeat_lease(task_queue, task_id: str, interval: float = 30) -> None:
    """Periodically renew a worker lease so it does not expire during
    long-running tool calls.  Runs until cancelled."""
    try:
        while True:
            await asyncio.sleep(interval)
            try:
                await task_queue.renew_lease(task_id)
            except Exception:
                pass
    except asyncio.CancelledError:
        pass
