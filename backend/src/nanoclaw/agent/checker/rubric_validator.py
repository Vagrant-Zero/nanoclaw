"""RubricValidator — validates rubric quality before use.

Checks performed:
1. At least one criterion exists
2. Each criterion has non-empty text
3. check_type is valid ("rule" or "llm")
4. Not all-LLM for purely operational subtasks (advisory warning)
5. Descriptions are specific enough to be evaluable
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nanoclaw.models.task import Rubric, Subtask


_VAGUE_PATTERNS = re.compile(
    r"\b(good|nice|proper|appropriate|correct|ok|fine)\b", re.IGNORECASE
)

_OPERATIONAL_TOOLS = frozenset({"read_file", "run_shell", "web_search"})


class RubricValidator:
    """Verify that a Rubric is well-formed and appropriate for its subtask."""

    def validate(
        self,
        subtask: Subtask,
        rubric: Rubric,
        user_request: str,  # noqa: ARG002 — kept for API compatibility
    ) -> tuple[list[str], list[str]]:
        """Validate a rubric. Returns (errors, warnings)."""
        errors: list[str] = []
        warnings: list[str] = []
        sid = subtask.id

        # 1. At least one criterion
        if not rubric.criteria:
            errors.append(f"[{sid}] Rubric has no criteria")
            return errors, warnings

        # 2. Each criterion has non-empty text
        for i, c in enumerate(rubric.criteria):
            if not c.text or not c.text.strip():
                errors.append(f"[{sid}] Criterion {i} has empty text")

        # 3. check_type must be valid
        valid_types = {"rule", "llm"}
        for i, c in enumerate(rubric.criteria):
            if c.check_type not in valid_types:
                errors.append(
                    f"[{sid}] Criterion {i} has invalid check_type: "
                    f"{c.check_type!r} (must be 'rule' or 'llm')"
                )

        # 4. Advisory: all-LLM for pure operational subtasks
        has_rule = any(c.check_type == "rule" for c in rubric.criteria)
        tool_names = set(subtask.tools_needed)
        if tool_names and tool_names.issubset(_OPERATIONAL_TOOLS) and not has_rule:
            warnings.append(
                f"[{sid}] Advisory: operational subtask has no rule-based "
                f"criteria; consider adding file/existence checks"
            )

        # 5. Check for vague wording in criteria
        for i, c in enumerate(rubric.criteria):
            if _VAGUE_PATTERNS.search(c.text):
                warnings.append(
                    f"[{sid}] Criterion {i} uses vague wording: {c.text!r}"
                )

        return errors, warnings
