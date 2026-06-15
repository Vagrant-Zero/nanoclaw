"""Checker — validates subtask results against their Rubrics.

Routing is based on ``Rubric.is_rule_only``:

* ``is_rule_only == True``  → rule-based checks (fast, no LLM call)
* ``is_rule_only == False`` → LLM-backed checks via ``response_format``

The full CheckerFeedback is available on the instance when a check fails,
so callers (Worker) can inspect why.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from nanoclaw.models.task import CheckResult, CheckerFeedback, Subtask

if TYPE_CHECKING:
    from nanoclaw.models.task import Criterion, Rubric


@dataclass
class CriterionResult:
    """Result of checking a single criterion within a Rubric."""

    text: str
    passed: bool
    reason: str = ""


# ── Default rule evaluators ──────────────────────────────────────────

# Pattern: (keyword list, evaluator function)
# Evaluator receives (criterion, subtask, result) → (passed, reason)

_RULE_EVALUATORS: list[tuple[re.Pattern, str]] = []


def _check_file_ops(criterion: Criterion, task: Subtask, result: str) -> tuple[bool, str]:  # noqa: ARG002
    """Rule check: file operation was successful (no error)."""
    if not result.strip():
        return False, "Result is empty"
    lower = result.lower()
    if "error:" in lower or "not found" in lower or "permission denied" in lower:
        return False, "File operation reported an error"
    return True, "File operation successful"


def _check_non_empty(criterion: Criterion, task: Subtask, result: str) -> tuple[bool, str]:  # noqa: ARG002
    """Rule check: result is non-empty."""
    if not result.strip():
        return False, "Result is empty"
    return True, "Output is non-empty"


def _check_no_error(criterion: Criterion, task: Subtask, result: str) -> tuple[bool, str]:  # noqa: ARG002
    """Rule check: result contains no error indicators."""
    if not result.strip():
        return False, "Result is empty"
    lower = result.lower()
    error_patterns = ["error:", "traceback", "exception:", "failed:", "exit code"]
    for pat in error_patterns:
        if pat in lower:
            return False, f"Error pattern detected: {pat}"
    return True, "No errors detected"


# Keyword → evaluator mapping
_RULE_EVALUATOR_MAP: list[tuple[set[str], Any]] = [
    ({"file", "read", "created", "written", "exists"}, _check_file_ops),
    ({"non-empty", "empty", "content", "output"}, _check_non_empty),
    ({"error", "exit", "return"}, _check_no_error),
]


# ── Checker class ────────────────────────────────────────────────────


class Checker:
    """Validates subtask execution results against their Rubrics.

    Usage::

        checker = Checker(llm=chat_model)
        result = await checker.check(subtask, "file contents...")
        if result.passed:
            await task_queue.complete(subtask.id, result_content)
        else:
            feedback = checker.last_feedback  # CheckerFeedback
            # ... re-route or re-plan
    """

    def __init__(self, llm: BaseChatModel | None = None) -> None:
        self._llm = llm
        self._last_criterion_results: list[CriterionResult] = []
        self._last_feedback: CheckerFeedback | None = None

    # ── Public API ──

    @property
    def last_criterion_results(self) -> list[CriterionResult]:
        """Per-criterion results from the most recent check."""
        return list(self._last_criterion_results)

    @property
    def last_feedback(self) -> CheckerFeedback | None:
        """Full CheckerFeedback from the most recent check (None if passed)."""
        return self._last_feedback

    async def check(
        self,
        subtask: Subtask,
        result: str,
        user_request: str = "",
    ) -> CheckResult:
        """Evaluate a subtask's result against its Rubric.

        Returns a ``CheckResult``. If the check fails, ``last_feedback``
        is populated with the full ``CheckerFeedback`` for re-planning.
        """
        # Reset state
        self._last_criterion_results = []
        self._last_feedback = None

        rubric = getattr(subtask, "rubric", None)
        if rubric is None:
            return CheckResult(passed=True, feedback="No rubric — skipping check")

        if rubric.is_rule_only:
            check_result = self._rule_check(subtask, result)
        else:
            check_result = await self._rubric_llm_check(subtask, result)

        # Populate feedback on failure
        if not check_result.passed:
            self._last_feedback = CheckerFeedback(
                subtask_id=subtask.id,
                check_result=check_result,
                rubric=rubric,
                result=result,
                user_request=user_request,
            )

        return check_result

    # ── Rule-based checking ──

    def _rule_check(self, subtask: Subtask, result: str) -> CheckResult:
        """Evaluate rule-type criteria against the result.

        Matches criterion text against known patterns (file ops, emptiness,
        error indicators) and applies the corresponding evaluator.
        """
        rubric = subtask.rubric
        if rubric is None:
            return CheckResult(passed=True, feedback="No rubric — skipping check")

        all_passed = True
        fail_reasons: list[str] = []
        criterion_results: list[CriterionResult] = []

        for criterion in rubric.criteria:
            if criterion.check_type != "rule":
                continue

            passed, reason = self._evaluate_rule(criterion, subtask, result)
            criterion_results.append(
                CriterionResult(text=criterion.text, passed=passed, reason=reason)
            )
            if not passed:
                all_passed = False
                fail_reasons.append(f"{criterion.text}: {reason}")

        self._last_criterion_results = criterion_results

        if not all_passed:
            feedback = "; ".join(fail_reasons)
            return CheckResult(
                passed=False,
                feedback=feedback,
                failure_category="execution",
            )

        return CheckResult(passed=True, feedback="All rule checks passed")

    def _evaluate_rule(
        self,
        criterion: Criterion,
        subtask: Subtask,
        result: str,
    ) -> tuple[bool, str]:
        """Evaluate a single rule-type criterion against the result.

        Uses keyword matching on the criterion text to pick an evaluator,
        then runs the evaluator. Falls through to default checks.
        """
        text_lower = criterion.text.lower()
        words = set(text_lower.split())

        # Try matching evaluators
        for keywords, evaluator in _RULE_EVALUATOR_MAP:
            if keywords & words:  # set intersection
                return evaluator(criterion, subtask, result)

        # Default: check for errors + non-empty
        if not result.strip():
            return False, "Result is empty"
        lower = result.lower()
        for pat in ["error:", "traceback", "exception:"]:
            if pat in lower:
                return False, f"Error pattern detected: {pat}"
        return True, "Default rule check passed"

    # ── LLM-based checking ──

    async def _rubric_llm_check(self, subtask: Subtask, result: str) -> CheckResult:
        """Evaluate rubric criteria via LLM.

        Sends the subtask description, rubric criteria, and result to the
        LLM and asks it to judge each criterion PASS or FAIL.
        """
        rubric = subtask.rubric
        if rubric is None:
            return CheckResult(passed=True, feedback="No rubric — skipping check")

        if self._llm is None:
            return CheckResult(
                passed=False,
                feedback="LLM not configured — cannot evaluate llm-type criteria",
            )

        # Build prompt
        criteria_lines = "\n".join(
            f"  {i+1}. [{c.check_type}] {c.text}"
            for i, c in enumerate(rubric.criteria)
        )

        system_prompt = SystemMessage(
            content=(
                "You are a quality checker. Evaluate whether a completed "
                "subtask meets the given rubric criteria.\n\n"
                f"Subtask: {subtask.description}\n\n"
                "Rubric:\n"
                f"{criteria_lines}\n\n"
                f"Result:\n{result[:3000]}\n\n"
                "For each criterion, determine PASS or FAIL and provide "
                "a brief reason.\n"
                'Respond with JSON: {"criteria_results": [\n'
                '  {"text": "<criterion text>", "passed": true, '
                '"reason": "..."}\n'
                "]}\n"
            )
        )

        # Call LLM with structured output
        try:
            response = await self._llm.ainvoke(
                [system_prompt],
                response_format={"type": "json_object"},
            )
        except Exception as exc:
            return CheckResult(
                passed=False,
                feedback=f"LLM check call failed: {exc}",
                failure_category="execution",
            )

        # Parse response
        try:
            data = json.loads(response.content)
            raw_results = data.get("criteria_results", [])
        except json.JSONDecodeError:
            return CheckResult(
                passed=False,
                feedback="Failed to parse LLM check response JSON",
                failure_category="execution",
            )

        # Process criterion results
        criterion_results: list[CriterionResult] = []
        all_passed = True

        for raw in raw_results:
            text = raw.get("text", "")
            raw_passed = raw.get("passed", False)
            reason = raw.get("reason", "")

            # Normalize boolean
            if isinstance(raw_passed, str):
                raw_passed = raw_passed.strip().upper() in ("PASS", "TRUE", "YES")
            passed = bool(raw_passed)

            if not passed:
                all_passed = False

            criterion_results.append(
                CriterionResult(text=text, passed=passed, reason=reason)
            )

        self._last_criterion_results = criterion_results

        # Determine overall pass/fail
        if rubric.require_all_pass:
            overall_passed = all_passed
        else:
            # Majority vote
            passed_count = sum(1 for cr in criterion_results if cr.passed)
            total = len(criterion_results) if criterion_results else 1
            overall_passed = passed_count > total / 2

        feedback_parts = [
            f"{'PASS' if cr.passed else 'FAIL'}: {cr.text}"
            for cr in criterion_results
        ]
        feedback = "\n".join(feedback_parts)

        if not overall_passed:
            return CheckResult(
                passed=False,
                feedback=feedback,
                failure_category="planning",
            )

        return CheckResult(passed=True, feedback=feedback)
