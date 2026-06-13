"""Iteration budget — cascading limits for retry/re-plan.

Thread-safe via asyncio.Lock. Enforces both per-subtask and global
iteration caps to prevent runaway retry loops.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field


@dataclass
class BudgetState:
    """Snapshot of budget state for SSE emission."""

    global_count: int
    global_max: int
    per_subtask: dict[str, int] = field(default_factory=dict)


class IterationBudget:
    """Cascading iteration limits (per-subtask + global).

    Usage::

        budget = IterationBudget(per_subtask_max=3, global_max=10)
        ok = await budget.try_consume("task_001")
        if not ok:
            # iteration_exhausted → notify user
            pass
    """

    def __init__(
        self,
        per_subtask_max: int = 3,
        global_max: int = 10,
    ) -> None:
        if per_subtask_max < 1:
            raise ValueError("per_subtask_max must be >= 1")
        if global_max < 1:
            raise ValueError("global_max must be >= 1")

        self.per_subtask_max = per_subtask_max
        self.global_max = global_max
        self._per_subtask_counts: dict[str, int] = {}
        self._global_count = 0
        self._lock = asyncio.Lock()

    async def try_consume(self, subtask_id: str) -> bool:
        """Try to consume one iteration slot.

        Returns True if the iteration is allowed (within limits).
        Returns False if either the per-subtask or global limit has been reached.
        """
        async with self._lock:
            if self._global_count >= self.global_max:
                return False
            sub_count = self._per_subtask_counts.get(subtask_id, 0)
            if sub_count >= self.per_subtask_max:
                return False
            self._per_subtask_counts[subtask_id] = sub_count + 1
            self._global_count += 1
            return True

    @property
    def state(self) -> BudgetState:
        """Return a snapshot of the current budget state.

        Useful for SSE ``iteration_exhausted`` events.
        """
        return BudgetState(
            global_count=self._global_count,
            global_max=self.global_max,
            per_subtask=dict(self._per_subtask_counts),
        )
