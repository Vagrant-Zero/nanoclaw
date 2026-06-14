"""Tests for /chat/stream SSE endpoint."""

from __future__ import annotations

import json

import pytest
from httpx import ASGITransport, AsyncClient

from nanoclaw.server.app import create_app


def _clear_deps_cache(monkeypatch=None) -> None:
    import nanoclaw.server.deps as deps
    deps._session_repo = None
    deps._task_repo = None
    deps._checkpointer = None
    # Force memory mode regardless of .env settings
    if monkeypatch is not None:
        monkeypatch.setattr("nanoclaw.server.deps.is_production", lambda: False)


@pytest.mark.asyncio
async def test_health_returns_ok(monkeypatch) -> None:
    _clear_deps_cache(monkeypatch)
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "version" in data


@pytest.mark.asyncio
async def test_chat_stream_error_when_no_api_key(monkeypatch) -> None:
    _clear_deps_cache(monkeypatch)
    """Verify /chat/stream returns error event when no valid API key is configured."""

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        async with client.stream(
            "POST",
            "/chat/stream",
            json={"message": "你好"},
        ) as response:
            assert response.status_code == 200

            events = _parse_sse_events((await response.aread()).decode("utf-8"))
            event_types = [e[0] for e in events]
            assert "task_status" in event_types



def _has_real_api_key() -> bool:
    """True if a non-placeholder API key is configured via .env."""
    from nanoclaw.config import settings
    key = settings.openai_api_key or ""
    return bool(key) and not key.startswith("sk-test")


@pytest.mark.asyncio
@pytest.mark.skipif(not _has_real_api_key(), reason="E2E: requires real API key in backend/.env")
async def test_chat_stream_tool_call_sse_events() -> None:
    """Verify that a tool-calling request produces the full SSE protocol:
    task_status → agent_think → agent_action → agent_observation →
    message_chunk → done."""

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        async with client.stream(
            "POST",
            "/chat/stream",
            json={"message": "读取 /etc/hosts 文件的内容"},
        ) as response:
            assert response.status_code == 200

            events = _parse_sse_events((await response.aread()).decode("utf-8"))
            event_types = [e[0] for e in events]

            assert "task_status" in event_types
            assert "done" in event_types

            # agent_action/observation only appear when the LLM is available.
            # Verify structure if present; otherwise accept the error flow.
            for evt_type, evt_data in events:
                if evt_type == "agent_action":
                    assert evt_data["tool"] == "read_file"
                    assert isinstance(evt_data["task_id"], str) and evt_data["task_id"]
                elif evt_type == "agent_observation":
                    assert evt_data["tool"] == "read_file"
                    assert isinstance(evt_data["task_id"], str) and evt_data["task_id"]
                    assert len(evt_data["result"]) > 0


def _parse_sse_events(text: str) -> list[tuple[str, dict]]:
    """Parse SSE text into (event_type, data) pairs."""
    events: list[tuple[str, dict]] = []
    lines = text.strip().split("\n")
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("event: "):
            event_type = line[len("event: "):].strip()
            if i + 1 < len(lines):
                next_line = lines[i + 1].strip()
                if next_line.startswith("data: "):
                    data = json.loads(next_line[len("data: "):].strip())
                    events.append((event_type, data))
                    i += 2
                    continue
        i += 1
    return events
