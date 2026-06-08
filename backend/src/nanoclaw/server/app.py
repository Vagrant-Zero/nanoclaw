"""FastAPI application factory."""

import asyncio
import json

from fastapi import FastAPI
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from nanoclaw.config import settings


class ChatRequest(BaseModel):
    message: str
    thread_id: str | None = None


class ToolCallInfo(BaseModel):
    name: str
    args: dict
    result: str | None = None


class ChatResponse(BaseModel):
    response: str
    thread_id: str | None
    tool_calls: list[ToolCallInfo]


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="Nanoclaw",
        version="0.1.0",
        description="Personal AI assistant API",
    )

    @app.get("/health")
    async def health():
        return {"status": "ok", "version": "0.1.0"}

    @app.post("/chat", response_model=ChatResponse)
    async def chat(req: ChatRequest) -> ChatResponse:
        """Simple chat — returns an echo response for now."""
        return ChatResponse(
            response=f"You said: {req.message}",
            thread_id=req.thread_id,
            tool_calls=[],
        )

    @app.post("/chat/stream")
    async def chat_stream(req: ChatRequest):
        """Mock SSE streaming chat."""

        async def event_generator():
            mock_response = f"Hello! You said: \"{req.message}\". This is a mock streaming response to demonstrate SSE."

            # 1) mock tool call
            yield {
                "event": "tool_call",
                "data": json.dumps({
                    "name": "mock_tool",
                    "args": {"query": req.message},
                    "id": "call_mock_001",
                }),
            }
            await asyncio.sleep(0.3)

            # 2) mock tool result
            yield {
                "event": "tool_result",
                "data": json.dumps({
                    "name": "mock_tool",
                    "result": f"Simulated result for: {req.message}",
                    "id": "call_mock_001",
                }),
            }
            await asyncio.sleep(0.3)

            # 3) stream response character by character
            for char in mock_response:
                yield {
                    "event": "message_chunk",
                    "data": json.dumps({"content": char}),
                }
                await asyncio.sleep(0.03)

            # 4) done
            yield {
                "event": "done",
                "data": json.dumps({"thread_id": req.thread_id or "mock_thread_001"}),
            }

        return EventSourceResponse(event_generator())

    return app
