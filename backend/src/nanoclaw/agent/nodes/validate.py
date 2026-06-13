"""Plan validation utilities — validates TaskPlan structure before execution.

Checks performed:
1. Unique subtask IDs (no duplicates)
2. Dependency reference integrity (all depends_on IDs exist)
3. Cycle detection (DFS visiting set)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nanoclaw.models.task import TaskPlan


def validate_plan(plan: TaskPlan) -> list[str]:
    """Validate a TaskPlan's structural integrity.

    Returns a list of error messages. An empty list means the plan is valid.
    """
    errors: list[str] = []
    subtasks = plan.subtasks
    if not subtasks:
        errors.append("Plan has no subtasks")
        return errors

    # 1. Check for duplicate IDs
    seen_ids: set[str] = set()
    for s in subtasks:
        if s.id in seen_ids:
            errors.append(f"Duplicate subtask ID: {s.id!r}")
        seen_ids.add(s.id)

    if errors:
        return errors  # Don't proceed if IDs aren't unique

    # 2. Check dependency reference integrity
    id_set = seen_ids  # Already built above
    for s in subtasks:
        for dep in s.depends_on:
            if dep not in id_set:
                errors.append(
                    f"Subtask {s.id!r} depends on unknown subtask ID: {dep!r}"
                )

    # 3. Cycle detection via DFS
    # Build adjacency list: tid -> list of dependencies
    adj: dict[str, list[str]] = {s.id: list(s.depends_on) for s in subtasks}

    visited: set[str] = set()
    path: set[str] = set()

    def _has_cycle(tid: str) -> bool:
        if tid in path:
            return True  # Back edge found → cycle
        if tid in visited:
            return False
        path.add(tid)
        visited.add(tid)
        for dep in adj.get(tid, []):
            if _has_cycle(dep):
                return True
        path.remove(tid)
        return False

    for tid in adj:
        if _has_cycle(tid):
            errors.append(f"Cycle detected in plan (involving subtask {tid!r})")
            break

    return errors
