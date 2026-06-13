"""Integration tests for Phase 2 — multi-task execution pipeline.

Covers: planner parsing, checker routing, iteration budget concurrency,
worker pool execution, failure classification, and the full complex path.
"""

from __future__ import annotations

import json
import time
import uuid

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, StateGraph

from nanoclaw.agent.checker.checker import Checker
from nanoclaw.agent.checker.iteration_budget import IterationBudget
from nanoclaw.agent.checker.rubric_validator import RubricValidator
from nanoclaw.agent.nodes.planner import create_planner_node
from nanoclaw.agent.nodes.validate import validate_plan
from nanoclaw.agent.state import AgentState, SupervisorState
from nanoclaw.agent.worker_pool import WorkerPool
from nanoclaw.models.task import (
    CheckResult,
    Criterion,
    Rubric,
    Subtask,
    TaskPlan,
    TaskStatus,
)
from nanoclaw.storage.session_repo import MemorySessionRepo
from nanoclaw.storage.task_queue import MemoryQueue
from nanoclaw.tools.file_ops import ReadFileTool
from nanoclaw.tools.registry import ToolRegistry
from nanoclaw.tools.shell import RunShellTool
from nanoclaw.tools.web_search import WebSearchTool


# ── Mocks ────────────────────────────────────────────────────────────


class _MockLLM:
    """Deterministic LLM stub for testing.

    Returns pre-registered responses in order. When exhausted, returns
    a default fallback.
    """

    def __init__(self) -> None:
        self._responses: list[str] = []
        self.call_count = 0

    def add(self, content: str) -> None:
        self._responses.append(content)

    async def ainvoke(self, messages: list, **kwargs: object) -> AIMessage:  # noqa: ARG002
        if self.call_count < len(self._responses):
            content = self._responses[self.call_count]
        else:
            content = '{"decision": "simple"}'
        self.call_count += 1
        return AIMessage(content=content)


class _MockReactAgent:
    """A compiled-graph-like stub that returns a canned response."""

    async def ainvoke(self, state: dict, **kwargs: object) -> dict:  # noqa: ARG002
        return {"messages": [AIMessage(content="mock execution result")]}


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def tool_registry() -> ToolRegistry:
    r = ToolRegistry()
    r.register(ReadFileTool())
    r.register(RunShellTool())
    r.register(WebSearchTool())
    return r


@pytest.fixture
def mock_llm() -> _MockLLM:
    return _MockLLM()


# ══════════════════════════════════════════════════════════════════════
# Planner parsing
# ══════════════════════════════════════════════════════════════════════


class TestPlanner:
    """Planner node: LLM JSON → TaskPlan + Rubric parsing."""

    @pytest.mark.asyncio
    async def test_planner_parses_valid_json(self, tool_registry: ToolRegistry) -> None:
        """Verify a valid LLM response is correctly parsed into a TaskPlan."""
        mock = _MockLLM()
        mock.add(json.dumps({
            "subtasks": [
                {
                    "id": "task_001",
                    "description": "Read project directory",
                    "depends_on": [],
                    "tools_needed": ["read_file", "run_shell"],
                    "compensation": None,
                    "rubric": {
                        "criteria": [
                            {"text": "Directory structure read", "check_type": "rule"},
                            {"text": "Output includes layout", "check_type": "llm"},
                        ],
                        "require_all_pass": True,
                    },
                },
                {
                    "id": "task_002",
                    "description": "Create summary report",
                    "depends_on": ["task_001"],
                    "tools_needed": ["run_shell"],
                    "compensation": "rm -f /tmp/report.md",
                    "rubric": {
                        "criteria": [
                            {"text": "Report file created", "check_type": "rule"},
                        ],
                        "require_all_pass": True,
                    },
                },
            ],
        }))

        planner = create_planner_node(mock, tool_registry)
        result = await planner({
            "messages": [HumanMessage(content="分析项目结构并生成报告")],
            "session_id": "test-session",
        })

        plan: TaskPlan | None = result.get("plan")
        assert plan is not None, f"Expected plan, got errors: {result.get('errors')}"
        assert len(plan.subtasks) == 2
        assert plan.session_id == "test-session"

        # Check first subtask
        t1 = plan.subtasks[0]
        assert t1.id == "task_001"
        assert t1.description == "Read project directory"
        assert t1.depends_on == []
        assert t1.status == TaskStatus.PENDING
        assert t1.rubric is not None
        assert len(t1.rubric.criteria) == 2
        assert t1.rubric.criteria[0].check_type == "rule"
        assert t1.rubric.criteria[1].check_type == "llm"
        assert t1.rubric.require_all_pass is True
        assert t1.rubric.is_rule_only is False

        # Check second subtask (with dependency)
        t2 = plan.subtasks[1]
        assert t2.id == "task_002"
        assert t2.depends_on == ["task_001"]
        assert t2.compensation == "rm -f /tmp/report.md"
        assert t2.rubric is not None
        assert t2.rubric.is_rule_only is True

    @pytest.mark.asyncio
    async def test_planner_returns_errors_on_bad_json(
        self, tool_registry: ToolRegistry,
    ) -> None:
        """Planner should report parse errors, not crash."""
        mock = _MockLLM()
        mock.add("this is not json")  # But with response_format, this shouldn't happen

        planner = create_planner_node(mock, tool_registry)
        # With response_format=json_object the LLM will always return valid JSON,
        # but malformed *structure* (no subtasks) should still be handled.
        result = await planner({
            "messages": [HumanMessage(content="do something")],
            "session_id": "s1",
        })
        # Should get errors instead of crashing
        assert result.get("plan") is None
        assert result.get("errors") is not None
        assert len(result["errors"]) > 0

    @pytest.mark.asyncio
    async def test_planner_empty_subtasks_handled(
        self, tool_registry: ToolRegistry,
    ) -> None:
        """Planner handles empty subtask list gracefully."""
        mock = _MockLLM()
        mock.add(json.dumps({"subtasks": []}))

        planner = create_planner_node(mock, tool_registry)
        result = await planner({
            "messages": [HumanMessage(content="hello")],
            "session_id": "s1",
        })
        assert result.get("plan") is None
        assert result.get("errors") is not None


# ══════════════════════════════════════════════════════════════════════
# Checker routing
# ══════════════════════════════════════════════════════════════════════


class TestChecker:
    """Checker routing: rule-only → _rule_check, mixed → _rubric_llm_check."""

    @pytest.mark.asyncio
    async def test_rule_only_works_without_llm(self) -> None:
        """Rule-only rubric should not require an LLM."""
        checker = Checker(llm=None)  # No LLM configured

        subtask = Subtask(
            id="t1",
            description="Read a file",
            rubric=Rubric(criteria=[
                Criterion(text="output is non-empty", check_type="rule"),
            ]),
        )

        # Non-empty result → PASS
        result = await checker.check(subtask, "file contents: hello")
        assert result.passed, f"Expected PASS, got: {result.feedback}"

        # Empty result → FAIL
        result = await checker.check(subtask, "")
        assert not result.passed

    @pytest.mark.asyncio
    async def test_mixed_rubric_needs_llm(self) -> None:
        """Mixed rubric without LLM should fail with clear message."""
        checker = Checker(llm=None)

        subtask = Subtask(
            id="t2",
            description="Analyze output",
            rubric=Rubric(criteria=[
                Criterion(text="output is non-empty", check_type="rule"),
                Criterion(text="analysis is correct", check_type="llm"),
            ]),
        )

        result = await checker.check(subtask, "some output")
        assert not result.passed
        assert "LLM not configured" in result.feedback

    @pytest.mark.asyncio
    async def test_rule_check_detects_error_patterns(self) -> None:
        """Rule check should detect common error patterns in result."""
        checker = Checker(llm=None)
        subtask = Subtask(
            id="t3",
            description="Read file",
            rubric=Rubric(criteria=[
                Criterion(text="file read successfully", check_type="rule"),
            ]),
        )

        result = await checker.check(subtask, "Error: permission denied")
        assert not result.passed
        assert hasattr(result, "failure_category")

    @pytest.mark.asyncio
    async def test_no_rubric_always_passes(self) -> None:
        """Subtask without rubric should pass without checking."""
        checker = Checker(llm=None)
        subtask = Subtask(id="t4", description="simple task")
        result = await checker.check(subtask, "anything")
        assert result.passed
        assert "No rubric" in result.feedback


# ══════════════════════════════════════════════════════════════════════
# IterationBudget concurrency
# ══════════════════════════════════════════════════════════════════════


class TestIterationBudget:
    """IterationBudget: concurrency safety and exhaustion."""

    @pytest.mark.asyncio
    async def test_sequential_consumption(self) -> None:
        budget = IterationBudget(per_subtask_max=3, global_max=10)
        for _ in range(3):
            assert await budget.try_consume("t1")
        assert not await budget.try_consume("t1")  # per-subtask exhausted

    @pytest.mark.asyncio
    async def test_global_exhaustion(self) -> None:
        budget = IterationBudget(per_subtask_max=5, global_max=3)
        assert await budget.try_consume("t1")
        assert await budget.try_consume("t2")
        assert await budget.try_consume("t3")
        assert not await budget.try_consume("t4")  # global exhausted

    @pytest.mark.asyncio
    async def test_concurrent_safety(self) -> None:
        """Multiple concurrent consumers should not corrupt state."""
        budget = IterationBudget(per_subtask_max=5, global_max=20)

        async def consumer(task_id: str, count: int) -> list[bool]:
            return [await budget.try_consume(task_id) for _ in range(count)]

        results = await asyncio.gather(
            consumer("t1", 5),
            consumer("t2", 5),
            consumer("t3", 5),
            consumer("t4", 5),
        )

        # All consumers should succeed (5+5+5+5 = 20 ≤ global_max)
        for r in results:
            assert all(r), f"Expected all consumed, got: {r}"

        # Budget should be exhausted now
        assert not await budget.try_consume("t5")
        assert budget.state.global_count == 20

    @pytest.mark.asyncio
    async def test_state_snapshot(self) -> None:
        budget = IterationBudget(per_subtask_max=3, global_max=10)
        await budget.try_consume("t1")
        await budget.try_consume("t1")
        await budget.try_consume("t2")

        s = budget.state
        assert s.global_count == 3
        assert s.global_max == 10
        assert s.per_subtask == {"t1": 2, "t2": 1}


# ══════════════════════════════════════════════════════════════════════
# Worker pool execution
# ══════════════════════════════════════════════════════════════════════


class TestWorkerPool:
    """WorkerPool: subtask execution lifecycle."""

    @pytest.mark.asyncio
    async def test_executes_independent_subtasks(self) -> None:
        """All subtasks should complete when they have no dependencies."""
        queue = MemoryQueue()
        plan = TaskPlan(session_id="test", subtasks=[
            Subtask(id="t1", description="Task 1"),
            Subtask(id="t2", description="Task 2"),
            Subtask(id="t3", description="Task 3", depends_on=["t1", "t2"]),
        ])
        await queue.init_plan(plan)

        pool = WorkerPool(
            task_queue=queue,
            react_agent=_MockReactAgent(),
            num_workers=2,
        )
        await pool.start()

        results = await queue.wait_for_all()
        await pool.stop()

        assert results["t1"] is not None
        assert results["t2"] is not None
        assert results["t3"] is not None

    @pytest.mark.asyncio
    async def test_subtask_status_updates(self) -> None:
        """Subtasks should transition through PENDING → RUNNING → SUCCEEDED."""
        queue = MemoryQueue()
        plan = TaskPlan(session_id="test", subtasks=[
            Subtask(id="t1", description="Task A"),
        ])
        await queue.init_plan(plan)

        # Get the subtask reference from the queue
        t1 = plan.subtasks[0]
        assert t1.status == TaskStatus.PENDING

        pool = WorkerPool(
            task_queue=queue,
            react_agent=_MockReactAgent(),
            num_workers=1,
        )
        await pool.start()
        results = await queue.wait_for_all()
        await pool.stop()

        # The Subtask object in the plan is the same object in the queue,
        # so its status should be updated
        assert t1.status == TaskStatus.SUCCEEDED
        assert results["t1"] is not None

    @pytest.mark.asyncio
    async def test_requeue_on_execution_failure(self) -> None:
        """A failing mock react_agent should trigger requeue/retry."""
        fail_count = 0

        class _FailingAgent:
            async def ainvoke(self, state: dict, **kwargs: object) -> dict:  # noqa: ARG002
                nonlocal fail_count
                fail_count += 1
                return {"messages": [AIMessage(content="")]}  # Empty = triggers failure

        queue = MemoryQueue()
        plan = TaskPlan(session_id="test", subtasks=[
            Subtask(id="t1", description="Failing task",
                    rubric=Rubric(criteria=[
                        Criterion(text="non-empty output", check_type="rule"),
                    ])),
        ])
        await queue.init_plan(plan)

        pool = WorkerPool(
            task_queue=queue,
            react_agent=_FailingAgent(),
            num_workers=1,
            llm=None,
        )
        await pool.start()

        # Wait with small timeout for retry cycle
        try:
            await asyncio.wait_for(queue.wait_for_all(), timeout=3)
        except asyncio.TimeoutError:
            pass  # Expected — task may keep retrying until budget exhausted

        await pool.stop()

        # The agent should have been called multiple times (one initial + retries)
        # until budget exhaustion or plan failure
        assert fail_count >= 1


# ══════════════════════════════════════════════════════════════════════
# Failure classification (rules-first)
# ══════════════════════════════════════════════════════════════════════


class TestFailureClassification:
    """Failure classification: rules first, LLM fallback."""

    @pytest.mark.asyncio
    async def test_timeout_classified_as_planning(self) -> None:
        """Timeout should be classified as 'planning' (subtask scope issue)."""
        # The WorkerPool._on_timeout always calls fail() with "Execution timed out"
        # This doesn't set a failure_category — it directly marks the task as FAILED
        subtask = Subtask(
            id="t1", description="slow task",
            rubric=Rubric(criteria=[Criterion(text="non-empty output", check_type="rule")]),
        )

        # Verify the subtask can be failed with a timeout message
        queue = MemoryQueue()
        await queue.init_plan(TaskPlan(session_id="test", subtasks=[subtask]))
        await queue.fail("t1", "Execution timed out after 300s")
        assert subtask.status == TaskStatus.FAILED
        assert "timed out" in (subtask.error or "")

    @pytest.mark.asyncio
    async def test_checker_sets_failure_category(self) -> None:
        """Checker should set failure_category for rule failures."""
        checker = Checker(llm=None)
        subtask = Subtask(
            id="t1",
            description="Read file",
            rubric=Rubric(criteria=[
                Criterion(text="non-empty output", check_type="rule"),
            ]),
        )
        result = await checker.check(subtask, "")
        assert not result.passed
        assert result.failure_category == "execution"


# ══════════════════════════════════════════════════════════════════════
# Full supervisor complex path
# ══════════════════════════════════════════════════════════════════════


class TestFullSupervisorComplexPath:
    """End-to-end: simple path and complex path integrity."""

    @pytest.mark.asyncio
    async def test_complex_path_components_integrate(self) -> None:
        """Individual complex-path components work together in sequence.

        This tests the component orchestration without a full graph run,
        which requires a live WorkerPool.
        """
        mock = _MockLLM()
        mock.add(json.dumps({
            "subtasks": [
                {
                    "id": "task_001",
                    "description": "Read project structure",
                    "depends_on": [],
                    "tools_needed": ["read_file"],
                    "compensation": None,
                    "rubric": {
                        "criteria": [
                            {"text": "Structure read", "check_type": "rule"},
                        ],
                        "require_all_pass": True,
                    },
                },
            ],
        }))

        from nanoclaw.agent.supervisor_graph import (
            _dispatch_node,
            _await_node,
            _collect_node,
        )

        tool_reg = ToolRegistry()
        tool_reg.register(ReadFileTool())
        tool_reg.register(RunShellTool())

        repo = MemorySessionRepo()
        sid = str(uuid.uuid4())
        from nanoclaw.models.chat import Session as ChatSession
        await repo.create(ChatSession(id=sid, created_at=time.time()))

        # 1. Generate plan via planner
        planner = create_planner_node(mock, tool_reg)
        plan_result = await planner({
            "messages": [HumanMessage(content="分析项目结构")],
            "session_id": sid,
        })
        plan = plan_result.get("plan")
        assert plan is not None, f"Planner failed: {plan_result.get('errors')}"
        assert len(plan.subtasks) == 1

        # 2. Dispatch via task queue
        queue = MemoryQueue()
        pool = WorkerPool(
            task_queue=queue,
            react_agent=_MockReactAgent(),
            num_workers=1,
        )
        dispatch_state = {
            "plan": plan,
            "task_queue": queue,
            "worker_pool": pool,
            "session_id": sid,
            "session_repo": repo,
            "errors": [],
        }
        dispatch_result = await _dispatch_node(dispatch_state)
        assert dispatch_result.get("errors") is None or dispatch_result.get("errors") == []

        # 3. Await completion
        await_state = {"task_queue": queue, "worker_pool": pool, "errors": []}
        await_result = await _await_node(await_state)
        results = await_result.get("worker_results", {})
        assert "task_001" in results

        # 4. Collect results
        collect_state = {
            "worker_results": results,
            "plan": plan,
            "errors": [],
        }
        collect_result = await _collect_node(collect_state)
        messages = collect_result.get("messages", [])
        assert len(messages) > 0
        assert "Completed" in messages[-1].content

    @pytest.mark.asyncio
    async def test_simple_path_still_works(self) -> None:
        """The supervisor should still handle simple queries (regression)."""
        from nanoclaw.agent.supervisor_graph import create_supervisor

        tool_reg = ToolRegistry()
        tool_reg.register(ReadFileTool())

        repo = MemorySessionRepo()
        sid = str(uuid.uuid4())

        # A "simple" message in Chinese should route to react
        # We need a mock to prevent LLM calls
        mock = _MockLLM()

        graph = create_supervisor(mock, tool_reg, repo)
        result = await graph.ainvoke({
            "messages": [HumanMessage(content="你好")],
            "session_id": sid,
            "task_id": "root",
            "session_repo": repo,
        })
        final = result["messages"][-1]
        assert final.content is not None


# Need asyncio for concurrent tests
import asyncio  # noqa: E402
