"""Web search tool — search via Tavily API (structured JSON, no HTML scraping).

Tavily is purpose-built for AI agents and returns clean {title, url, content}
results without needing HTML parsing or dealing with bot detection.
"""

from __future__ import annotations

import os

import httpx

from nanoclaw.config import settings
from nanoclaw.tools.base import BaseTool, ToolSpec


class WebSearchTool(BaseTool):
    """Search the web via Tavily API."""

    spec = ToolSpec(
        name="web_search",
        description="Search the web for information. Returns top search result snippets.",
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of results to return (default 3)",
                },
            },
            "required": ["query"],
        },
    )

    _URL = "https://api.tavily.com/search"
    _TIMEOUT = httpx.Timeout(connect=10, read=15, write=10, pool=10)

    def run(self, query: str, max_results: int = 3) -> str:
        api_key = settings.tavily_api_key or os.environ.get("TAVILY_API_KEY", "")
        if not api_key:
            return (
                "Search unavailable: TAVILY_API_KEY is not configured. "
                "Set it in .env or NANOCLAW_TAVILY_API_KEY environment variable."
            )

        try:
            resp = httpx.post(
                self._URL,
                json={
                    "api_key": api_key,
                    "query": query,
                    "search_depth": "advanced",
                    "max_results": min(max_results, 10),
                },
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "Nanoclaw/1.0",
                },
                timeout=self._TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 402:
                return "Search unavailable: Tavily API quota exceeded."
            return f"Search error: Tavily returned HTTP {exc.response.status_code}"
        except httpx.TimeoutException:
            return "Search timed out while contacting Tavily."
        except Exception as exc:
            return f"Search error: {exc}"

        results = data.get("results", [])
        if not results:
            return "No results found."

        formatted: list[str] = []
        for i, r in enumerate(results[:max_results]):
            title = r.get("title", "").strip()
            url = r.get("url", "").strip()
            content = r.get("content", "").strip()
            if not title and not content:
                continue
            parts = [f"{i+1}. {title or url}"]
            if url:
                parts.append(f"   {url}")
            if content:
                parts.append(f"   {content[:500]}")
            formatted.append("\n".join(parts))

        if not formatted:
            return "No results found."

        return "\n\n".join(formatted)


# Re-register the tool in the registry
from nanoclaw.tools.registry import ToolRegistry as _ToolRegistry
_initialised = False

def register_search_tool(registry: _ToolRegistry) -> None:
    """Register WebSearchTool on a ToolRegistry."""
    global _initialised
    if _initialised:
        return
    registry.register(WebSearchTool())
    _initialised = True
