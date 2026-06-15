"""Web search tool — single-engine search with timeout, retry, and graceful fallback.

Engine: Sogou (https://www.sogou.com) — Chinese search engine that works
reliably behind the GFW with server-side rendered HTML.

If search fails, returns a user-friendly message rather than hanging or
crashing the agent.
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

    # ── Sogou ──────────────────────────────────────────────────────

    def _extract_sogou(self, html: str, max_count: int) -> list[tuple[str, str, str]]:
        results: list[tuple[str, str, str]] = []
        # Sogou results: each result is in a <div class="vrwrap"> block
        blocks = re.split(r'<div[^>]*class="[^"]*vrwrap[^"]*"[^>]*>', html)[1:]
        for block in blocks[:max_count * 2]:
            # URL + title from <a> inside <h3 class="vr-title">
            title_m = re.search(
                r'<h3[^>]*>.*?<a[^>]*href="(https?://[^"]+)"[^>]*>(.*?)</a>.*?</h3>',
                block, re.DOTALL,
            )
            if not title_m:
                # Fallback: look for <a> with href
                title_m = re.search(
                    r'<a[^>]*href="(https?://[^"]+)"[^>]*>(.*?)</a>',
                    block, re.DOTALL,
                )
            url = title_m.group(1) if title_m else ""
            title = self._clean(title_m.group(2)) if title_m else ""

            if not title or not url:
                continue
            # Skip Sogou internal links
            if "sogou.com" in url and "/web" not in url:
                continue

            snippet = ""
            # Try multiple snippet patterns Sogou uses
            for pat in (
                r'<p[^>]*class="str_info"[^>]*>(.*?)</p>',
                r'<p[^>]*class="str-text"[^>]*>(.*?)</p>',
                r'<div[^>]*class="str-text"[^>]*>(.*?)</div>',
                r'<div[^>]*class="space-txt"[^>]*>(.*?)</div>',
            ):
                sm = re.search(pat, block, re.DOTALL)
                if sm:
                    snippet = self._clean(sm.group(1))
                    if snippet:
                        break

            results.append((title, url, snippet))
            if len(results) >= max_count:
                break
        return results


# ── Engine list ─────────────────────────────────────────────────────

# Sogou — Chinese search engine, server-rendered, works behind GFW.
_ENGINES = [
    _Engine("sogou", "https://www.sogou.com/web", "GET", "query"),
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

    _TIMEOUT = httpx.Timeout(connect=10, read=10, write=10, pool=10)

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
            _headers = {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            }
            if engine.method == "POST":
                resp = client.post(
                    req["url"],
                    data=req["data"],
                    headers=_headers,
                    follow_redirects=True,
                )
            else:
                # Add Referer to avoid Baidu bot detection
                _headers["Referer"] = "https://www.baidu.com/"
                resp = client.get(
                    req["url"],
                    params=req["params"] if "params" in req else {},
                    headers=_headers,
                    follow_redirects=True,
                )
            resp.raise_for_status()

        snippets = engine.extract(resp.text, max_results)
        if not snippets:
            import logging
            _log = logging.getLogger(__name__)
            _log.warning(
                "web_search [%s]: HTTP %d, %d bytes — but no results extracted. "
                "HTML preview: %.100s...",
                engine.name, resp.status_code, len(resp.text),
                resp.text[:100],
            )
            return None  # Let next engine try

        return "\n\n".join(
            f"{i+1}. {title}\n   {url}\n   {snippet}"
            for i, (title, url, snippet) in enumerate(snippets)
        )
