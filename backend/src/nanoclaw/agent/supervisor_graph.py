"""Supervisor graph — top-level agent orchestrator.

Phase 1 (simple path):  Router → ReAct Node → output
Phase 2 (complex path): Router → Planner → Dispatch → Await → Collect

The router classifies each request as simple or complex. Simple requests
go through the existing ReAct loop directly. Complex requests are
decomposed into a subtask DAG by the Planner, dispatched to a WorkerPool,
awaited, and collected into a final response.
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage
from langgraph.graph import END, StateGraph

from nanoclaw.agent.nodes.planner import create_planner_node
from nanoclaw.agent.nodes.react_agent import create_react_agent
from nanoclaw.agent.nodes.router import create_router_node
from nanoclaw.agent.state import SupervisorState
from nanoclaw.models.task import TaskStatus
from nanoclaw.storage.session_repo import SessionRepository
from nanoclaw.tools.registry import ToolRegistry


# ── Complex-path node implementations ──


async def _dispatch_node(state: dict) -> dict:
    """Initialize the TaskQueue with the plan and start workers."""
    plan = state.get("plan")
    if plan is None:
        errs = (state.get("errors") or []) + ["No plan to dispatch"]
        return {"errors": errs}

    task_queue = state.get("task_queue")
    worker_pool = state.get("worker_pool")

    if task_queue is None:
        errs = (state.get("errors") or []) + ["Task queue not configured"]
        return {"errors": errs}
    if worker_pool is None:
        errs = (state.get("errors") or []) + ["Worker pool not configured"]
        return {"errors": errs}

    await task_queue.init_plan(plan)
    worker_pool.session_id = state.get("session_id", "")
    worker_pool.session_repo = state.get("session_repo")

    # Emit agent_plan so TUI can display the subtask DAG
    tasks_list = [
        {
            "id": s.id,
            "description": s.description,
            "status": s.status.value,
            "depends_on": list(s.depends_on),
        }
        for s in plan.subtasks
    ]
    await worker_pool.emit_event("agent_plan", {
        "tasks": tasks_list,
        "session_id": state.get("session_id", ""),
    })

    await worker_pool.start()

    return {}


async def _await_node(state: dict) -> dict:
    """Wait for all subtasks to complete, then stop workers."""
    task_queue = state.get("task_queue")
    if task_queue is None:
        errs = (state.get("errors") or []) + ["Task queue not initialized"]
        return {"errors": errs}

    results = await task_queue.wait_for_all()

    worker_pool = state.get("worker_pool")
    if worker_pool is not None:
        await worker_pool.stop()

    return {"worker_results": results}


async def _collect_node(state: dict) -> dict:
    """Aggregate subtask results into a final response message."""
    results = state.get("worker_results") or {}
    plan = state.get("plan")
    state_errors = state.get("errors") or []

    if plan is None:
        return {
            "messages": [
                AIMessage(
                    content="I wasn't able to generate a plan for your request."
                )
            ]
        }

    succeeded = []
    failed = []
    for s in plan.subtasks:
        if s.status == TaskStatus.SUCCEEDED:
            succeeded.append(s)
        elif s.status in (TaskStatus.FAILED, TaskStatus.CANCELLED):
            failed.append(s)

    parts: list[str] = []
    total = len(plan.subtasks)

    if succeeded:
        parts.append(f"Completed {len(succeeded)} of {total} subtasks.")
        for s in succeeded:
            result = (results.get(s.id) or "")[:300]
            parts.append(f"\n  \u2022 {s.description}")
            if result:
                parts.append(f": {result}")

    if failed:
        parts.append(f"\n\nFailed or cancelled: {len(failed)} subtask(s).")
        for s in failed:
            err = s.error or "No error details"
            parts.append(f"\n  \u2022 {s.id}: {err}")

    if state_errors:
        parts.append(f"\n\nSystem errors: {'; '.join(state_errors)}")

    return {"messages": [AIMessage(content="".join(parts))]}


# ── Supervisor factory ──


def create_supervisor(
    llm: Any,
    tool_registry: ToolRegistry,
    session_repo: SessionRepository,
) -> Any:
    """Create the compiled Supervisor LangGraph with both paths.

    Args:
        llm: LangChain chat model.
        tool_registry: All available tools.
        session_repo: Session storage for persistence.

    Returns:
        A compiled ``langgraph.graph.CompiledStateGraph``.

    Graph structure::

        START → [router] ──"react"──→ [react_node] → END
                          └─"plan"──→ [planner] → [dispatch]
                                        → [await_results] → [collect] → END
    """
    router = create_router_node(llm)
    planner = create_planner_node(llm, tool_registry)
    react_subgraph = create_react_agent(llm, tool_registry)

    builder = StateGraph(SupervisorState)

    builder.add_node("router", router)
    builder.add_node("react", react_subgraph)
    builder.add_node("planner", planner)
    builder.add_node("dispatch", _dispatch_node)
    builder.add_node("await_results", _await_node)
    builder.add_node("collect", _collect_node)

    builder.set_entry_point("router")

    builder.add_conditional_edges(
        "router",
        lambda s: s["router_decision"],
        {
            "react": "react",
            "plan": "planner",
        },
    )

    # Simple path
    builder.add_edge("react", END)

    # Complex path
    builder.add_edge("planner", "dispatch")
    builder.add_edge("dispatch", "await_results")
    builder.add_edge("await_results", "collect")
    builder.add_edge("collect", END)

    return builder.compile()
