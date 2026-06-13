"""RubricValidator — validates rubric quality before use.

Stub for Phase 2, Task 5.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nanoclaw.models.task import Rubric, Subtask


class RubricValidator:
    """Verify that a Rubric is well-formed and covers its subtask."""

    def validate(self, subtask: Subtask, rubric: Rubric, user_request: str) -> list[str]:
        """Return list of issues (empty = valid)."""
        return []  # Stub — always valid
