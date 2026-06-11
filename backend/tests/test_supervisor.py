"""Tests for supervisor graph and router routing logic."""

from __future__ import annotations

import pytest
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI

from nanoclaw.agent.supervisor_graph import create_supervisor, router_node
from nanoclaw.storage.session_repo import MemorySessionRepo
from nanoclaw.tools.registry import ToolRegistry


class TestRouterNode:
    """Heuristic routing: short/simple → react, long+complex → plan."""

    def test_trivial_greeting_routes_to_react(self) -> None:
        state = {"messages": [HumanMessage(content="你好")]}
        assert router_node(state) == "react"

    def test_short_query_routes_to_react(self) -> None:
        state = {"messages": [HumanMessage(content="当前时间")]}
        assert router_node(state) == "react"

    def test_complex_analysis_routes_to_plan(self) -> None:
        state = {"messages": [HumanMessage(content="请你分析一下这个项目的安全漏洞并生成一份报告")]}
        assert router_node(state) == "plan"

    def test_keyword_without_length_routes_to_react(self) -> None:
        # "分析" alone is too short → react
        state = {"messages": [HumanMessage(content="分析")]}
        assert router_node(state) == "react"

    def test_long_no_keyword_routes_to_react(self) -> None:
        state = {"messages": [HumanMessage(content="今天天气怎么样，我想出去走走看看，外面风大不大")]}
        assert router_node(state) == "react"


class TestCreateSupervisor:
    """Supervisor graph structure and compilation."""

    def test_graph_compiles_with_expected_nodes(self) -> None:
        llm = ChatOpenAI(
            model="deepseek-chat",
            api_key="test",
            base_url="https://api.deepseek.com",
        )
        registry = ToolRegistry()
        repo = MemorySessionRepo()
        graph = create_supervisor(llm, registry, repo)
        nodes = list(graph.nodes.keys())
        assert "router" in nodes
        assert "react" in nodes
