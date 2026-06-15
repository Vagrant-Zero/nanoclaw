"""Web search tool — multi-engine search with timeout, retry, and graceful fallback.

Engines tried in order:
1. Bing (https://www.bing.com) — reliable for most queries
2. Baidu (https://www.baidu.com) — works in restricted network environments
3. DuckDuckGo (https://html.duckduckgo.com) — final fallback

Each engine has a 10s timeout. If all engines fail, returns a user-friendly
message rather than hanging or crashing the agent.
"""

from __future__ import annotations

import re
import time
from urllib.parse import quote_plus

import httpx

from nanoclaw.tools.base import BaseTool, ToolSpec


# ── Per-engine configuration ─────────────────────────────────────────

class _Engine:
    """Configuration for a single search engine."""

    __slots__ = ("name", "url", "method", "query_key")

    def __init__(self, name: str, url: str, method: str, query_key: str) -> None:
        self.name = name
        self.url = url
        self.method = method.upper()
        self.query_key = query_key

    def build_request(self, query: str) -> dict:
        if self.method == "POST":
            return {"url": self.url, "data": {self.query_key: query}}
        return {"url": self.url, "params": {self.query_key: query}}

    def extract(self, html: str, max_count: int) -> list[tuple[str, str, str]]:
        """Extract (title, url, snippet) tuples from engine HTML."""
        extractor = getattr(self, f"_extract_{self.name}", None)
        if extractor:
            return extractor(html, max_count)
        return []

    @staticmethod
    def _clean(text: str) -> str:
        return re.sub(r"<[^>]+>", "", text).strip()

    # ── Bing ────────────────────────────────────────────────────────

    def _extract_bing(self, html: str, max_count: int) -> list[tuple[str, str, str]]:
        results: list[tuple[str, str, str]] = []
        # Bing results are in <li class="b_algo"> blocks
        blocks = re.split(r'<li[^>]*class="b_algo"[^>]*>', html)[1:]
        for block in blocks[:max_count]:
            title_m = re.search(r'<a[^>]*href="(https?://[^"]+)"[^>]*>(.*?)</a>', block, re.DOTALL)
            snippet_m = re.search(r'<p[^>]*>(.*?)</p>', block, re.DOTALL)
            title = self._clean(title_m.group(2)) if title_m else ""
            url = title_m.group(1) if title_m else ""
            snippet = self._clean(snippet_m.group(1)) if snippet_m else ""
            if title and url:
                results.append((title, url, snippet))
        return results

    # ── Baidu ───────────────────────────────────────────────────────

    def _extract_baidu(self, html: str, max_count: int) -> list[tuple[str, str, str]]:
        results: list[tuple[str, str, str]] = []
        # Baidu results are in <div class="result c-container"> blocks
        blocks = re.split(r'<div[^>]*class="result[^"]*c-container[^"]*"[^>]*>', html)[1:]
        for block in blocks[:max_count]:
            title_m = re.search(r'<a[^>]*href="(https?://[^"]+)"[^>]*>(.*?)</a>', block, re.DOTALL)
            snippet_m = re.search(r'<div[^>]*class="c-abstract"[^>]*>(.*?)</div>', block, re.DOTALL)
            if not snippet_m:
                snippet_m = re.search(r'<span[^>]*class="content-right_[^"]*"[^>]*>(.*?)</span>', block, re.DOTALL)
            title = self._clean(title_m.group(2)) if title_m else ""
            url = title_m.group(1) if title_m else ""
            snippet = self._clean(snippet_m.group(1)) if snippet_m else ""
            if title and url:
                results.append((title, url, snippet))
        return results

    # ── DuckDuckGo ──────────────────────────────────────────────────

    def _extract_duckduckgo(self, html: str, max_count: int) -> list[tuple[str, str, str]]:
        results: list[tuple[str, str, str]] = []
        blocks = re.split(r'<div class="result[^"]*"', html)[1:] if '<div class="result' in html else []
        for block in blocks[:max_count]:
            title_m = re.search(r'class="result__a"[^>]*>(.*?)</a>', block, re.DOTALL)
            url_m = re.search(r'href="(https?://[^"]+)"', block)
            snippet_m = re.search(r'class="result__snippet"[^>]*>(.*?)</(?:a|div)>', block, re.DOTALL)
            title = self._clean(title_m.group(1)) if title_m else ""
            url = url_m.group(1) if url_m else ""
            snippet = self._clean(snippet_m.group(1)) if snippet_m else ""
            if title and url:
                results.append((title, url, snippet))
        return results


# ── Engine list ─────────────────────────────────────────────────────

_ENGINES = [
    _Engine("bing", "https://www.bing.com/search", "GET", "q"),
    _Engine("baidu", "https://www.baidu.com/s", "GET", "wd"),
    _Engine("duckduckgo", "https://html.duckduckgo.com/html/", "POST", "q"),
]


# ── Tool implementation ──────────────────────────────────────────────

class WebSearchTool(BaseTool):
    """Search the web using multiple engines with timeout and retry."""

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

    _TIMEOUT = httpx.Timeout(connect=10, read=10, pool=10)

    def run(self, query: str, max_results: int = 3) -> str:
        last_error: str | None = None

        for engine in _ENGINES:
            try:
                result = self._try_engine(engine, query, max_results)
                if result:
                    return result
            except Exception as exc:
                last_error = f"{engine.name}: {exc}"
                continue

        # ── Graceful fallback — all engines failed ──
        msg = (
            f"Unable to retrieve web search results for '{query[:100]}'. "
            f"All search engines are currently unreachable"
        )
        if last_error:
            msg += f" ({last_error})"
        msg += ". Please try again later or use local knowledge."
        return msg

    def _try_engine(
        self, engine: _Engine, query: str, max_results: int
    ) -> str | None:
        """Try a single engine. Returns formatted results string, or None if no results."""
        req = engine.build_request(query)
        with httpx.Client(timeout=self._TIMEOUT) as client:
            if engine.method == "POST":
                resp = client.post(
                    req["url"],
                    data=req["data"],
                    headers={"User-Agent": "Mozilla/5.0"},
                    follow_redirects=True,
                )
            else:
                resp = client.get(
                    req["url"],
                    params=req["params"] if "params" in req else {},
                    headers={"User-Agent": "Mozilla/5.0"},
                    follow_redirects=True,
                )
            resp.raise_for_status()

        snippets = engine.extract(resp.text, max_results)
        if not snippets:
            return None  # Let next engine try

        return "\n\n".join(
            f"{i+1}. {title}\n   {url}\n   {snippet}"
            for i, (title, url, snippet) in enumerate(snippets)
        )
