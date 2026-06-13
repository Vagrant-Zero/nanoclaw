"""ReAct agent graph — core LLM + tool execution loop.

This graph is the fundamental building block of Nanoclaw's agent:
- Phase 1: Used directly as the simple-path responder
- Phase 2+: Embedded inside each Worker for subtask execution

When an ``sse_callback`` is provided, the agent emits ``agent_think``,
``agent_action``, and ``agent_observation`` events during execution.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Callable

from langgraph.graph import END, StateGraph

from nanoclaw.agent.state import AgentState
from nanoclaw.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

# Timeout for a single LLM call
_LLM_TIMEOUT_SECONDS = 30


def create_react_agent(
    llm: Any,
    tool_registry: ToolRegistry,
    llm_timeout: float = _LLM_TIMEOUT_SECONDS,
    sse_callback: Callable[[str, dict], Awaitable[None]] | None = None,
) -> Any:
    """Create a compiled ReAct LangGraph with the given LLM and tools.

    The graph follows the standard ReAct loop::

        agent (LLM call) → tools if tool_calls → agent ... → END

    When *sse_callback* is provided it is called after each LLM
    response (``agent_think`` / ``agent_action``) and after each tool
    execution (``agent_observation``).

    Args:
        llm: A LangChain chat model instance supporting .ainvoke().
        tool_registry: Registry containing all available tools.
        llm_timeout: Timeout in seconds for each LLM call.
        sse_callback: Optional async callback ``(event, data) -> None``
            for emitting SSE events during execution.

    Returns:
        A compiled ``langgraph.graph.CompiledStateGraph``.
    """
    tool_node = tool_registry.get_tool_node()
    openai_tools = tool_registry.to_openai_dicts()

    async def call_model(state: AgentState) -> dict[str, list]:
        """Invoke the LLM and emit SSE events if callback is set."""
        async with asyncio.timeout(llm_timeout):
            response = await llm.ainvoke(state["messages"], tools=openai_tools)

        # Emit SSE events
        if sse_callback is not None:
            task_id = state.get("task_id", "root")
            if response.content:
                await sse_callback(
                    "agent_think",
                    {"content": response.content, "task_id": task_id},
                )
            if hasattr(response, "tool_calls") and response.tool_calls:
                for tc in response.tool_calls:
                    name = (
                        tc.get("name", "")
                        if isinstance(tc, dict)
                        else getattr(tc, "name", "")
                    )
                    args = (
                        tc.get("args", {})
                        if isinstance(tc, dict)
                        else getattr(tc, "args", {})
                    )
                    await sse_callback(
                        "agent_action",
                        {"tool": name, "args": args, "task_id": task_id},
                    )

        return {"messages": [response]}

    async def call_tools(state: AgentState) -> dict:
        """Execute tools and emit agent_observation events."""
        result = await tool_node.ainvoke(state)

        if sse_callback is not None:
            task_id = state.get("task_id", "root")
            new_msgs = result.get("messages", [])
            for msg in new_msgs:
                tool_name = getattr(msg, "name", "unknown")
                content = str(getattr(msg, "content", ""))[:2000]
                await sse_callback(
                    "agent_observation",
                    {"tool": tool_name, "result": content, "task_id": task_id},
                )

        return result

    def should_continue(state: AgentState) -> str:
        """Route to tools if LLM requested tool calls, otherwise end."""
        last_message = state["messages"][-1]
        if hasattr(last_message, "tool_calls") and last_message.tool_calls:
            return "tools"
        return "end"

    builder = StateGraph(AgentState)
    builder.add_node("agent", call_model)
    builder.add_node("tools", call_tools)
    builder.set_entry_point("agent")
    builder.add_conditional_edges(
        "agent",
        should_continue,
        {"tools": "tools", "end": END},
    )
    builder.add_edge("tools", "agent")

    return builder.compile()
