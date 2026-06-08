"""Tool registry — register, retrieve, and convert tools to LangChain format."""

import inspect
from typing import Any

from langchain_core.tools import StructuredTool

from nanoclaw.tools.base import BaseTool


class ToolRegistry:
    """Central registry for all available tools."""

    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        """Register a tool instance."""
        self._tools[tool.spec.name] = tool

    def get(self, name: str) -> BaseTool:
        """Retrieve a tool by name."""
        tool = self._tools.get(name)
        if tool is None:
            raise KeyError(f"Unknown tool: {name}")
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
        """Convert all registered tools to LangChain StructuredTool instances."""
        result: list[StructuredTool] = []
        for t in self._tools.values():

            def tool_fn(**kwargs: Any) -> str:
                return t.run(**kwargs)

            tool_fn.__name__ = t.spec.name
            tool_fn.__doc__ = t.spec.description

            # Build signature from JSON schema properties
            params: list[inspect.Parameter] = []
            properties = t.spec.parameters.get("properties", {})
            required = t.spec.parameters.get("required", [])
            for pname, pdef in properties.items():
                annotation = str  # default
                ptype = pdef.get("type", "string")
                if ptype == "string":
                    annotation = str
                elif ptype in ("integer", "number"):
                    annotation = int
                elif ptype == "boolean":
                    annotation = bool
                default = inspect.Parameter.empty if pname in required else None
                params.append(
                    inspect.Parameter(pname, inspect.Parameter.KEYWORD_ONLY, default=default, annotation=annotation)
                )
            tool_fn.__signature__ = inspect.Signature(params)  # type: ignore[attr-defined]

            result.append(
                StructuredTool.from_function(
                    name=t.spec.name,
                    description=t.spec.description,
                    func=tool_fn,
                )
            )
        return result
