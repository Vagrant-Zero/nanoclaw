"""Tool registry — register, retrieve, and convert tools to LangChain format."""

from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool
from langchain_core.utils.function_calling import convert_to_openai_tool

from nanoclaw.tools.base import BaseTool


class ToolRegistry:
    """Central registry for all available tools.

    Tools are registered once at application startup and never modified
    per-request — this keeps the tools parameter stable for KV caching.
    """

    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        """Register a tool instance."""
        self._tools[tool.spec.name] = tool

    def get(self, name: str) -> BaseTool:
        """Retrieve a tool by name."""
        tool = self._tools.get(name)
        if tool is None:
            msg = f"Unknown tool: {name!r}"
            raise KeyError(msg)
        return tool

    def list(self) -> list[dict[str, Any]]:
        """List all registered tools as plain dicts (for introspection)."""
        return [
            {
                "name": t.spec.name,
                "description": t.spec.description,
                "parameters": t.spec.parameters,
            }
            for t in self._tools.values()
        ]

    def to_langchain(self) -> list[StructuredTool]:
        """Convert all registered tools to LangChain StructuredTool instances.

        Used by ToolNode (LangGraph prebuilt) for tool execution.
        """

        def _make_wrapper(tool: BaseTool):
            def _run(**kwargs: Any) -> str:
                return tool.run(**kwargs)

            return _run

        return [
            StructuredTool(
                name=t.spec.name,
                description=t.spec.description,
                args_schema=t.spec.parameters,
                func=_make_wrapper(t),
            )
            for t in self._tools.values()
        ]

    def to_openai_dicts(self) -> list[dict[str, Any]]:
        """Convert tools to OpenAI-compatible dicts for LLM.ainvoke().

        Uses convert_to_openai_tool to produce serializable dicts — required
        because openai>=2.0 fails to serialize StructuredTool function objects.
        The StructuredTool list is still used by ToolNode for execution.
        """
        return [convert_to_openai_tool(t) for t in self.to_langchain()]

    def get_tool_node(self):
        """Return a LangGraph ToolNode pre-configured with all registered tools."""
        from langgraph.prebuilt import ToolNode

        return ToolNode(self.to_langchain())
