"""Planner node — decomposes complex requests into subtask DAGs with rubrics.

Uses an LLM call (with ``response_format="json_object"``) to generate a
structured plan. Each subtask carries a Rubric so the Checker (Task 5)
can verify completion quality without additional LLM inference on the
planner's intent.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Callable

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from nanoclaw.agent.checker.rubric_validator import RubricValidator
from nanoclaw.agent.nodes.validate import validate_plan
from nanoclaw.models.task import (
    Criterion,
    Rubric,
    Subtask,
    TaskPlan,
    TaskStatus,
)

if TYPE_CHECKING:
    from nanoclaw.agent.state import SupervisorState
    from nanoclaw.tools.registry import ToolRegistry


# ── Prompt builder ───────────────────────────────────────────────────


def _build_planner_prompt(tools_info: list[dict]) -> SystemMessage:
    """Build the system prompt instructing the LLM to output a structured plan."""
    tool_lines = "\n".join(
        f"  - {t['name']}: {t['description']}"
        for t in tools_info
    )

    prompt = (
        "You are a task planner. Decompose the user's request into "
        "subtasks.\n\n"
        "Rules:\n"
        "1. Each subtask MUST have: id, description, depends_on, "
        "tools_needed, compensation, rubric\n"
        "2. Dependencies: if task_B needs task_A's output, "
        'task_B.depends_on = ["task_001"]\n'
        "3. First subtasks have empty depends_on (run immediately)\n"
        "4. Maximum 8 subtasks per plan\n"
        "5. Each subtask SHOULD be self-contained (one logical unit of work)\n"
        "6. tools_needed lists tool names from the available tool list below\n"
        "7. compensation: a shell command to UNDO the subtask's side effects. "
        "Can be null if read-only.\n"
        "8. Each subtask MUST include a \"rubric\" object with:\n"
        '   - "criteria": list of pass/fail criteria\n'
        "     Each criterion has:\n"
        '     - "text": description of what to check\n'
        '     - "check_type": "rule" or "llm"\n'
        '       "rule" = code-level checks (file exists, exit code zero)\n'
        '       "llm"  = needs LLM judgment (correctness, completeness)\n'
        '   - "require_all_pass": true or false\n'
        "9. Output a valid JSON object with a \"subtasks\" array, no other keys.\n\n"
        "Example:\n"
        '{"subtasks": [\n'
        '  {\n'
        '    "id": "task_001",\n'
        '    "description": "Read project src directory structure",\n'
        '    "depends_on": [],\n'
        '    "tools_needed": ["read_file", "run_shell"],\n'
        '    "compensation": null,\n'
        '    "rubric": {\n'
        '      "criteria": [\n'
        '        {"text": "Directory structure read and recorded", '
        '"check_type": "rule"},\n'
        '        {"text": "Output includes top-level layout", '
        '"check_type": "llm"}\n'
        "      ],\n"
        '      "require_all_pass": true\n'
        "    }\n"
        "  }\n"
        "]}\n\n"
        "Available tools:\n"
        f"{tool_lines}\n"
    )

    return SystemMessage(content=prompt)


# ── Planner node factory ─────────────────────────────────────────────


def create_planner_node(
    llm: BaseChatModel,
    tool_registry: ToolRegistry,
) -> Callable:
    """Create an async planner node for the Supervisor graph.

    The returned function accepts a SupervisorState-compatible dict and
    returns ``{"plan": TaskPlan | None, "errors": list[str] | None}``.

    Plan generation flow::

        user message + tool list
          → LLM (response_format="json_object")
          → parse JSON → list[Subtask] with Rubrics
          → RubricValidator.check() on each
          → validate_plan() (structure + cycle check)
          → return TaskPlan or errors
    """

    rubric_validator = RubricValidator()

    async def planner_node(state: dict) -> dict:
        messages = state.get("messages", [])
        if not messages:
            return {"plan": None, "errors": ["No messages to plan from"]}

        last = messages[-1]
        content = getattr(last, "content", "") or ""
        tools_info = tool_registry.list()

        # ── 1. Call LLM with structured output ──
        system_msg = _build_planner_prompt(tools_info)
        try:
            response = await llm.ainvoke(
                [system_msg, HumanMessage(content=content)],
                response_format={"type": "json_object"},
            )
        except Exception as exc:
            return {"plan": None, "errors": [f"Planner LLM call failed: {exc}"]}

        # ── 2. Parse JSON response ──
        try:
            data = json.loads(response.content)
        except json.JSONDecodeError as exc:
            return {"plan": None, "errors": [f"Planner JSON parse error: {exc}"]}

        raw_items: list[dict] = []
        if isinstance(data, dict) and "subtasks" in data:
            raw_items = data["subtasks"]
        elif isinstance(data, list):
            raw_items = data
        else:
            return {
                "plan": None,
                "errors": [
                    "Planner response missing 'subtasks' array"
                ],
            }

        # ── 3. Build Subtask objects with Rubrics ──
        subtasks: list[Subtask] = []
        parse_errors: list[str] = []

        for item in raw_items:
            sid = item.get("id", "")
            if not sid:
                parse_errors.append("Subtask entry missing 'id' — skipped")
                continue

            # Parse rubric
            rubric: Rubric | None = None
            raw_rubric = item.get("rubric")
            if isinstance(raw_rubric, dict):
                criteria = []
                for c in raw_rubric.get("criteria", []):
                    criteria.append(
                        Criterion(
                            text=c.get("text", ""),
                            check_type=c.get("check_type", "llm"),
                        )
                    )
                rubric = Rubric(
                    criteria=criteria,
                    require_all_pass=raw_rubric.get("require_all_pass", True),
                )

            subtasks.append(
                Subtask(
                    id=sid,
                    description=item.get("description", ""),
                    status=TaskStatus.PENDING,
                    depends_on=item.get("depends_on", []),
                    tools_needed=item.get("tools_needed", []),
                    compensation=item.get("compensation"),
                    max_retries=3,
                    rubric=rubric,
                )
            )

        if not subtasks:
            return {
                "plan": None,
                "errors": parse_errors or ["No valid subtasks parsed from plan"],
            }

        # ── 4. Build TaskPlan ──
        session_id = state.get("session_id") or "unknown"
        plan = TaskPlan(session_id=session_id, subtasks=subtasks)

        # ── 5. Validate each subtask's rubric ──
        user_content = content
        for s in subtasks:
            if s.rubric:
                v_errors = rubric_validator.validate(s, s.rubric, user_content)
                if v_errors:
                    return {
                        "plan": None,
                        "errors": [
                            f"Rubric validation for {s.id}: {e}"
                            for e in v_errors
                        ],
                    }

        # ── 6. Validate plan structure (IDs, refs, cycles) ──
        validation_errors = validate_plan(plan)
        if validation_errors:
            return {"plan": None, "errors": validation_errors}

        state_errors: list[str] | None = parse_errors if parse_errors else None
        return {"plan": plan, "errors": state_errors}

    return planner_node
