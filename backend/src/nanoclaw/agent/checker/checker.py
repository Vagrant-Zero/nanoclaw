"""Checker — validates subtask results against Rubrics.

Stub for Phase 2, Task 5.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nanoclaw.models.task import CheckResult, Rubric, Subtask


@dataclass
class CheckerFeedback:
    """Full context packaged when a check fails."""

    subtask_id: str
    check_result: CheckResult
    rubric: Rubric
    result: str
    trace_path: str | None = None
    user_request: str = ""


class Checker:
    """Validates subtask execution results against a Rubric.

    Routes to rule-based or LLM-based checking depending on
    the rubric's check_type values.
    """

    def __init__(self) -> None:
        self._results: dict[str, list[dict]] = {}

    def check(self, subtask: Subtask, result: str) -> CheckResult:
        """Evaluate subtask result against its rubric."""
        from nanoclaw.models.task import CheckResult

        if subtask.rubric is None:
            return CheckResult(passed=True, feedback="No rubric defined — skipping check")
        return CheckResult(passed=True, feedback="Stub check — always passes")
