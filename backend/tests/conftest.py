"""Shared test fixtures for nanoclaw backend."""

import pytest_asyncio
from httpx import ASGITransport, AsyncClient


@pytest_asyncio.fixture
async def api_client():
    """ASGI client for testing the FastAPI app.

    Sets OPENAI_API_KEY so ChatOpenAI doesn't raise on startup.
    """
    import os

    os.environ.setdefault("OPENAI_API_KEY", "sk-test-key")

    from nanoclaw.server.app import create_app

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
