"""Router node — classifies user requests as simple or complex.

Uses a heuristic-first strategy with LLM fallback for ambiguous cases.
Heuristic rules handle ~80% of traffic without incurring LLM call costs.
The LLM fallback uses response_format to guarantee clean JSON output.
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

# ── Heuristic thresholds ──────────────────────────────────────────────

_FUZZY_THRESHOLD = 15       # Messages shorter than this are always "react"
_MIN_COMPLEX_LENGTH = 20     # Messages need this length + keyword match for "plan"

# ── Keywords ──────────────────────────────────────────────────────────

_SIMPLE_KEYWORDS = frozenset({
    "hello", "hi", "hey", "good morning", "good evening",
    "what time", "what's the time", "date", "who are you",
    "thanks", "thank you", "bye", "goodbye", "yes", "no",
})

_COMPLEX_KEYWORDS = frozenset({
    "analyze", "analyse", "compare", "investigate", "research",
    "survey", "explore", "plan", "design", "build", "create",
    "develop", "implement", "refactor", "debug", "fix",
    "optimize", "migrate",
    # Chinese complex keywords
    "总结", "分析", "比较", "规划", "设计", "实现", "调查", "研究",
})


# ── LLM fallback prompt ──────────────────────────────────────────────

_FALLBACK_SYSTEM_PROMPT = SystemMessage(
    content=(
        "You are a request classifier. Determine if the user's request "
        "is 'simple' or 'complex'.\n\n"
        "Simple: greetings, time/date queries, short factual questions, "
        "yes/no questions, thank-yous.\n"
        "Complex: multi-step tasks, analysis, comparison, research, "
        "planning, implementation, debugging.\n\n"
        'Respond with a JSON object: {"decision": "simple"} or '
        '{"decision": "complex"}. No other text.'
    )
)


async def _llm_fallback(llm: Any, content: str) -> str:
    """Call LLM to classify ambiguous requests. Returns 'react' or 'plan'.

    Uses ``response_format={"type": "json_object"}`` to force the API
    to return parseable JSON, avoiding brittle system-prompt-only parsing.
    """
    response = await llm.ainvoke(
        [_FALLBACK_SYSTEM_PROMPT, HumanMessage(content=content)],
        response_format={"type": "json_object"},
    )
    try:
        data = json.loads(response.content)
        return "plan" if data.get("decision") == "complex" else "react"
    except (json.JSONDecodeError, KeyError):
        # Should be extremely rare with response_format enabled
        return "react"


def create_router_node(llm: Any):
    """Create an async router node for the Supervisor graph.

    The returned function accepts a LangGraph state dict and returns
    ``{"router_decision": "react" | "plan"}`` to drive the conditional edge.

    Decision flow::

        simple keyword match  ──→ "react"
        content < 15 chars    ──→ "react"
        complex keyword + 20+ ──→ "plan"
        otherwise             ──→ LLM fallback → "react" | "plan"
    """

    async def router_node(state: dict) -> dict[str, str]:
        messages = state.get("messages", [])
        if not messages:
            return {"router_decision": "react"}

        last = messages[-1]
        content = (getattr(last, "content", "") or "").strip().lower()

        # 1. Simple keyword match (exact or starts-with)
        if any(content == kw or content.startswith(kw) for kw in _SIMPLE_KEYWORDS):
            return {"router_decision": "react"}

        # 2. Very short queries → always simple
        if len(content) < _FUZZY_THRESHOLD:
            return {"router_decision": "react"}

        # 3. Complex keyword match with sufficient length
        if any(kw in content for kw in _COMPLEX_KEYWORDS):
            if len(content) >= _MIN_COMPLEX_LENGTH:
                return {"router_decision": "plan"}
            # Otherwise fall through to LLM (ambiguous length)

        # 4. Ambiguous → LLM fallback
        decision = await _llm_fallback(llm, content)
        return {"router_decision": decision}

    return router_node
