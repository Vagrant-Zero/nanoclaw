"""Web search tool — search the web and fetch page content."""

import re
from urllib.parse import quote_plus

import httpx

from nanoclaw.tools.base import BaseTool, ToolSpec


class WebSearchTool(BaseTool):
    """Search the web via DuckDuckGo's HTML interface."""

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

    _SEARCH_URL = "https://html.duckduckgo.com/html/"

    def run(self, query: str, max_results: int = 3) -> str:
        try:
            response = httpx.post(
                self._SEARCH_URL,
                data={"q": query},
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=15,
            )
            response.raise_for_status()

            # Simple HTML extraction of result snippets
            snippets = self._extract_snippets(response.text, max_results)
            if not snippets:
                return "No results found."

            return "\n\n".join(
                f"{i+1}. {title}\n   {url}\n   {snippet}"
                for i, (title, url, snippet) in enumerate(snippets)
            )
        except httpx.HTTPError as exc:
            return f"Search error: {exc}"
        except Exception as exc:
            return f"Search error: {exc}"

    def _extract_snippets(self, html: str, max_count: int) -> list[tuple[str, str, str]]:
        results: list[tuple[str, str, str]] = []
        # Extract result blocks using simple regex patterns
        # Find all result links with their snippets
        blocks = re.split(r'<div class="result[^"]*"', html)[1:] if '<div class="result' in html else []

        for block in blocks[:max_count]:
            # Extract title
            title_match = re.search(r'class="result__a"[^>]*>(.*?)</a>', block, re.DOTALL)
            # Extract URL
            url_match = re.search(r'href="(https?://[^"]+)"', block)
            # Extract snippet
            snippet_match = re.search(r'class="result__snippet"[^>]*>(.*?)</(?:a|div)>', block, re.DOTALL)

            title = re.sub(r"<[^>]+>", "", title_match.group(1)).strip() if title_match else ""
            url = url_match.group(1) if url_match else ""
            snippet = re.sub(r"<[^>]+>", "", snippet_match.group(1)).strip() if snippet_match else ""

            if title and url:
                results.append((title, url, snippet))

        return results
