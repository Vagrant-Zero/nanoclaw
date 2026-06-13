"""ReAct agent graph — core LLM + tool execution loop.

When ``context_manager`` is provided, the LLM prompt is assembled with
memory context (user profile, skills).  When ``event_logger`` is provided,
``llm_call`` and ``tool_call`` events are recorded.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any, Callable

from langgraph.graph import END, StateGraph

from nanoclaw.agent.state import AgentState
from nanoclaw.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from nanoclaw.context.manager import ContextManager
    from nanoclaw.eval.logger import EventLogger

_LLM_TIMEOUT_SECONDS = 30


def create_react_agent(
    llm: Any,
    tool_registry: ToolRegistry,
    llm_timeout: float = _LLM_TIMEOUT_SECONDS,
    sse_callback: Callable[[str, dict], Awaitable[None]] | None = None,
    context_manager: Any | None = None,
    event_logger: Any | None = None,
) -> Any:
    """Create a compiled ReAct LangGraph.

    Args:
        llm: LangChain chat model.
        tool_registry: Tool registry.
        llm_timeout: Per-LLM-call timeout.
        sse_callback: ``(event, data)`` callback for SSE events.
        context_manager: If set, ``build_prompt()`` is used instead of raw messages.
        event_logger: If set, ``llm_call``/``tool_call`` events are logged.
    """
    tool_node = tool_registry.get_tool_node()
    openai_tools = tool_registry.to_openai_dicts()

    # ── Agent node ───────────────────────────────────────────────

    async def call_model(state: AgentState) -> dict[str, list]:
        async with asyncio.timeout(llm_timeout):
            if context_manager is not None:
                from nanoclaw.context.manager import SessionContext
                from nanoclaw.models.chat import ChatMessage

                chat_msgs = _to_chat_msgs(state.get("messages", []))
                ctx = SessionContext(
                    id=state.get("session_id", "") or "",
                    messages=chat_msgs,
                )
                prompt_list = await context_manager.build_prompt(ctx)
                response = await llm.ainvoke(prompt_list, tools=openai_tools)
            else:
                response = await llm.ainvoke(
                    state["messages"], tools=openai_tools
                )

        # Log llm_call
        if event_logger is not None:
            sid = state.get("session_id") or "unknown"
            tid = state.get("task_id") or "root"
            await event_logger.log_event(sid, "llm_call", {
                "task_id": tid,
                "model": getattr(llm, "model_name", "unknown"),
                "input_tokens": 0,
                "output_tokens": 0,
                "duration_ms": 0,
            })

        # SSE events
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

    # ── Tools node ──────────────────────────────────────────────

    async def call_tools(state: AgentState) -> dict:
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

        # Log tool_call events
        if event_logger is not None:
            sid = state.get("session_id") or "unknown"
            tid = state.get("task_id") or "root"
            new_msgs = result.get("messages", [])
            for msg in new_msgs:
                await event_logger.log_event(sid, "tool_call", {
                    "task_id": tid,
                    "tool_name": getattr(msg, "name", "unknown"),
                    "args_summary": "",
                    "result_summary": str(getattr(msg, "content", ""))[:200],
                    "duration_ms": 0,
                })

        return result

    # ── Routing ─────────────────────────────────────────────────

    def should_continue(state: AgentState) -> str:
        last_message = state["messages"][-1]
        if hasattr(last_message, "tool_calls") and last_message.tool_calls:
            return "tools"
        return "end"

    # ── Graph build ──────────────────────────────────────────────

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


# ── Helpers ──────────────────────────────────────────────────────


def _to_chat_msgs(lc_messages: list) -> list:
    """Convert LangChain BaseMessage list to ChatMessage list."""
    from nanoclaw.models.chat import ChatMessage

    result: list = []
    for m in lc_messages:
        mtype = getattr(m, "type", "")
        if mtype == "human":
            role = "user"
        elif mtype == "ai":
            role = "assistant"
        elif mtype == "system":
            role = "system"
        else:
            role = "user"
        result.append(
            ChatMessage(role=role, content=getattr(m, "content", "") or "")
        )
    return result
