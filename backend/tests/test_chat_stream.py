"""Tests for /chat/stream SSE endpoint."""

from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from nanoclaw.server.app import create_app


@pytest.mark.asyncio
async def test_health_returns_ok() -> None:
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
    """Verify /chat/stream returns error event when no API key is configured."""

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

            text = (await response.aread()).decode("utf-8")
            lines = text.strip().split("\n")

            events: list[tuple[str, dict]] = []
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

            event_types = [e[0] for e in events]
            assert "task_status" in event_types
