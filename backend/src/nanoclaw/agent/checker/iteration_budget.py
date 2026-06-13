"""Iteration budget — cascading limits for retry/re-plan.

Stub for Phase 2, Task 5.
"""

from __future__ import annotations

import asyncio


class IterationBudget:
    """Cascading iteration limits (per-subtask + global).

    Thread-safe via asyncio.Lock.
    """

    def __init__(
        self,
        per_subtask_max: int = 3,
        global_max: int = 10,
    ) -> None:
        self.per_subtask_max = per_subtask_max
        self.global_max = global_max
        self._per_subtask_counts: dict[str, int] = {}
        self._global_count = 0
        self._lock = asyncio.Lock()

    async def try_consume(self, subtask_id: str) -> bool:
        """Try to consume one iteration slot. Returns True if allowed."""
        async with self._lock:
            if self._global_count >= self.global_max:
                return False
            sub_count = self._per_subtask_counts.get(subtask_id, 0)
            if sub_count >= self.per_subtask_max:
                return False
            self._per_subtask_counts[subtask_id] = sub_count + 1
            self._global_count += 1
            return True
