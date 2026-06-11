"""ReAct agent graph — core LLM + tool execution loop.

This graph is the fundamental building block of Nanoclaw's agent:
- Phase 1: Used directly as the simple-path responder
- Phase 2+: Embedded inside each Worker for subtask execution

The graph is built as a factory function that injects LLM and tool
dependencies at construction time (not in AgentState), keeping the
state pure and serializable. Tools are passed as raw dicts to LLM
(required for openai>=2.0 compatibility).
"""

from __future__ import annotations

import asyncio
from typing import Any

from langgraph.graph import END, StateGraph

from nanoclaw.agent.state import AgentState
from nanoclaw.tools.registry import ToolRegistry

# Timeout for a single LLM call
_LLM_TIMEOUT_SECONDS = 30


def create_react_agent(
    llm: Any,
    tool_registry: ToolRegistry,
    llm_timeout: float = _LLM_TIMEOUT_SECONDS,
) -> Any:
    """Create a compiled ReAct LangGraph with the given LLM and tools.

    The graph follows the standard ReAct loop:

        agent (LLM call) → tools if tool_calls → agent (LLM call) → ... → END

    Args:
        llm: A LangChain chat model instance supporting .ainvoke().
        tool_registry: Registry containing all available tools.

    Returns:
        A compiled langgraph.graph.CompiledStateGraph.
    """
    tool_node = tool_registry.get_tool_node()
    openai_tools = tool_registry.to_openai_dicts()

    async def call_model(state: AgentState) -> dict[str, list]:
        """Invoke the LLM with current message history and available tools.

        Wrapped in asyncio.timeout to prevent hanging indefinitely.
        """
        async with asyncio.timeout(llm_timeout):
            response = await llm.ainvoke(state["messages"], tools=openai_tools)
        return {"messages": [response]}

    def should_continue(state: AgentState) -> str:
        """Route to tools if LLM requested tool calls, otherwise end."""
        last_message = state["messages"][-1]
        if hasattr(last_message, "tool_calls") and last_message.tool_calls:
            return "tools"
        return "end"

    builder = StateGraph(AgentState)
    builder.add_node("agent", call_model)
    builder.add_node("tools", tool_node)
    builder.set_entry_point("agent")
    builder.add_conditional_edges(
        "agent",
        should_continue,
        {"tools": "tools", "end": END},
    )
    builder.add_edge("tools", "agent")

    return builder.compile()
