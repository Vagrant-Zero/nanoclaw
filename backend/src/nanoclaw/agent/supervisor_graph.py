"""Supervisor graph — top-level agent orchestrator.

Phase 1 (simple path): Router → ReAct Node → output
Phase 2+ (complex path): Router → Planner → Dispatch → (Worker Pool) → Await → Collect

Router determines complexity heuristically (keyword match + message length),
avoiding LLM calls for trivial queries. LLM fallback added in Phase 2.
"""

from __future__ import annotations

from typing import Any, Literal

from langgraph.graph import END, StateGraph

from nanoclaw.agent.nodes.react_agent import create_react_agent
from nanoclaw.agent.state import AgentState
from nanoclaw.storage.session_repo import SessionRepository
from nanoclaw.tools.registry import ToolRegistry

# Thresholds for heuristic routing
_MIN_COMPLEX_LENGTH = 20
_COMPLEX_KEYWORDS = frozenset({
    "分析", "报告", "比较", "计划",
    "explore", "analyze", "investigate", "research", "survey",
    "总结", "规划",
})


def router_node(state: AgentState) -> Literal["react", "plan"]:
    """Route user input: simple → react, complex → plan (Phase 2).

    Phase 1 always routes to "react". The "plan" path is reserved for
    Phase 2 and will never be reached until the Supervisor graph adds
    planner/dispatch/collect nodes.
    """
    last_message = state["messages"][-1]
    content = getattr(last_message, "content", "") or ""
    if any(kw in content for kw in _COMPLEX_KEYWORDS) and len(content) > _MIN_COMPLEX_LENGTH:
        return "plan"
    return "react"


def create_supervisor(
    llm: Any,
    tool_registry: ToolRegistry,
    session_repo: SessionRepository,
) -> Any:
    """Create the compiled Supervisor LangGraph.

    Args:
        llm: LangChain chat model (injected at construction, not in State).
        tool_registry: All available tools (fixed for entire session → KV cache stable).
        session_repo: Session storage for persistence.

    Returns:
        A compiled langgraph.graph.CompiledStateGraph.

    Phase 1 graph structure:

        START → [router] → "react" → [react_node] → END
                    └→ "plan"  → (reserved for Phase 2)
    """
    react_subgraph = create_react_agent(llm, tool_registry)

    builder = StateGraph(AgentState)
    builder.add_node("router", router_node)
    builder.add_node("react", react_subgraph)
    builder.set_entry_point("router")
    builder.add_conditional_edges(
        "router",
        lambda s: s,
        {
            "react": "react",
            "plan": END,  # Phase 1: plan path terminates; Phase 2: routes to planner
        },
    )
    builder.add_edge("react", END)

    return builder.compile()
