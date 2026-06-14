"""Tests for supervisor graph and router routing logic."""

from __future__ import annotations

import time
import uuid

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_openai import ChatOpenAI

from nanoclaw.agent.nodes.router import create_router_node
from nanoclaw.agent.supervisor_graph import create_supervisor
from nanoclaw.models.chat import Session as ChatSession
from nanoclaw.config import settings
from nanoclaw.storage.session_repo import MemorySessionRepo

# Skip E2E tests when no API key is configured
_e2e_skip = not bool(settings.openai_api_key)
from nanoclaw.tools.file_ops import ReadFileTool
from nanoclaw.tools.registry import ToolRegistry
from nanoclaw.tools.shell import RunShellTool
from nanoclaw.tools.web_search import WebSearchTool


class _MockLLM:
    """Mock LLM that returns a predefined JSON decision for router tests."""

    def __init__(self, decision: str = "simple") -> None:
        self._decision = decision

    async def ainvoke(self, messages, **kwargs):  # noqa: ARG002
        return AIMessage(content=f'{{"decision": "{self._decision}"}}')


def _make_registry() -> ToolRegistry:
    r = ToolRegistry()
    r.register(ReadFileTool())
    r.register(RunShellTool())
    r.register(WebSearchTool())
    return r


class TestRouterNode:
    """Heuristic routing: short/simple → react, long+complex → plan."""

    @pytest.mark.asyncio
    async def test_trivial_greeting_routes_to_react(self) -> None:
        router = create_router_node(None)
        state = {"messages": [HumanMessage(content="你好")]}
        result = await router(state)
        assert result["router_decision"] == "react"

    @pytest.mark.asyncio
    async def test_short_query_routes_to_react(self) -> None:
        router = create_router_node(None)
        state = {"messages": [HumanMessage(content="当前时间")]}
        result = await router(state)
        assert result["router_decision"] == "react"

    @pytest.mark.asyncio
    async def test_complex_analysis_routes_to_plan(self) -> None:
        router = create_router_node(None)
        state = {"messages": [HumanMessage(content="请你分析一下这个项目的安全漏洞并生成一份报告")]}
        result = await router(state)
        assert result["router_decision"] == "plan"

    @pytest.mark.asyncio
    async def test_keyword_without_length_routes_to_react(self) -> None:
        router = create_router_node(None)
        state = {"messages": [HumanMessage(content="分析")]}
        result = await router(state)
        assert result["router_decision"] == "react"

    @pytest.mark.asyncio
    async def test_long_weather_query_routes_to_react(self) -> None:
        """Weather query is complex length but not analysis → LLM says simple."""
        mock_llm = _MockLLM(decision="simple")
        router = create_router_node(mock_llm)
        state = {
            "messages": [
                HumanMessage(content="今天天气怎么样，我想出去走走看看，外面风大不大")
            ]
        }
        result = await router(state)
        assert result["router_decision"] == "react"


class TestCreateSupervisor:
    """Supervisor graph structure and compilation."""

    def test_graph_compiles_with_all_phase2_nodes(self) -> None:
        llm = ChatOpenAI(
            model="deepseek-chat",
            api_key="sk-test",
            base_url="https://api.deepseek.com",
        )
        graph = create_supervisor(llm, _make_registry(), MemorySessionRepo())
        nodes = list(graph.nodes.keys())
        assert "router" in nodes
        assert "react" in nodes
        assert "planner" in nodes
        assert "dispatch" in nodes
        assert "await_results" in nodes
        assert "collect" in nodes

@pytest.mark.skipif(_e2e_skip, reason="NANOCLAW_OPENAI_API_KEY or OPENAI_API_KEY not set in .env")
class TestE2E:
    """End-to-end: graph invoke through DeepSeek API."""

    @pytest.mark.asyncio
    async def test_simple_greeting_returns_response(self) -> None:
        from nanoclaw.config import settings

        llm = ChatOpenAI(
            model=settings.llm_model,
            api_key=settings.openai_api_key,
            base_url=settings.llm_base_url,
        )
        repo = MemorySessionRepo()
        graph = create_supervisor(llm, _make_registry(), repo)
        sid = str(uuid.uuid4())
        await repo.create(ChatSession(id=sid, created_at=time.time()))

        result = await graph.ainvoke({
            "messages": [HumanMessage(content="你好，请用中文简短回复")],
            "session_id": sid,
            "task_id": "root",
            "session_repo": repo,
        })
        final_msg = result["messages"][-1]
        assert final_msg.content
        assert len(final_msg.content) > 0
