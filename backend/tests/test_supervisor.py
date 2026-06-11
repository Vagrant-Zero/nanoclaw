"""Tests for supervisor graph and router routing logic."""

from __future__ import annotations

import time
import uuid
import pytest
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI

from nanoclaw.agent.supervisor_graph import create_supervisor, router_node
from nanoclaw.models.chat import Session as ChatSession
from nanoclaw.storage.session_repo import MemorySessionRepo
from nanoclaw.tools.file_ops import ReadFileTool
from nanoclaw.tools.registry import ToolRegistry
from nanoclaw.tools.shell import RunShellTool
from nanoclaw.tools.web_search import WebSearchTool


def _make_registry() -> ToolRegistry:
    r = ToolRegistry()
    r.register(ReadFileTool())
    r.register(RunShellTool())
    r.register(WebSearchTool())
    return r


class TestRouterNode:
    """Heuristic routing: short/simple → react, long+complex → plan."""

    def test_trivial_greeting_routes_to_react(self) -> None:
        state = {"messages": [HumanMessage(content="你好")]}
        result = router_node(state)
        assert result["router_decision"] == "react"

    def test_short_query_routes_to_react(self) -> None:
        state = {"messages": [HumanMessage(content="当前时间")]}
        assert router_node(state)["router_decision"] == "react"

    def test_complex_analysis_routes_to_plan(self) -> None:
        state = {"messages": [HumanMessage(content="请你分析一下这个项目的安全漏洞并生成一份报告")]}
        assert router_node(state)["router_decision"] == "plan"

    def test_keyword_without_length_routes_to_react(self) -> None:
        state = {"messages": [HumanMessage(content="分析")]}
        assert router_node(state)["router_decision"] == "react"

    def test_long_no_keyword_routes_to_react(self) -> None:
        state = {"messages": [HumanMessage(content="今天天气怎么样，我想出去走走看看，外面风大不大")]}
        assert router_node(state)["router_decision"] == "react"


class TestCreateSupervisor:
    """Supervisor graph structure and compilation."""

    def test_graph_compiles_with_expected_nodes(self) -> None:
        llm = ChatOpenAI(
            model="deepseek-chat",
            api_key="sk-test",
            base_url="https://api.deepseek.com",
        )
        graph = create_supervisor(llm, _make_registry(), MemorySessionRepo())
        nodes = list(graph.nodes.keys())
        assert "router" in nodes
        assert "react" in nodes


class TestE2E:
    """End-to-end: graph invoke through DeepSeek API."""

    @pytest.mark.asyncio
    async def test_simple_greeting_returns_response(self) -> None:
        llm = ChatOpenAI(
            model="deepseek-chat",
            api_key="sk-0c90f95c01fe41e1afdcf494eb3a1c11",
            base_url="https://api.deepseek.com",
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
