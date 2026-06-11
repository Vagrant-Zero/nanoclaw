"""FastAPI application factory."""

from __future__ import annotations

import json
import time
import uuid
from typing import AsyncGenerator

from fastapi import FastAPI
from langchain_core.messages import HumanMessage
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from nanoclaw.config import settings
from nanoclaw.models.chat import ChatMessage, Session as ChatSession
from nanoclaw.server.deps import (
    get_llm,
    get_session_repo,
    get_supervisor,
    get_tool_registry,
)


class ChatRequest(BaseModel):
    message: str
    thread_id: str | None = None


class HealthResponse(BaseModel):
    status: str
    version: str


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

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(status="ok", version="0.1.0")

    @app.post("/chat", response_model=ChatResponse)
    async def chat(req: ChatRequest) -> ChatResponse:
        """Simple chat — returns an echo response for now."""
        return ChatResponse(
            response=f"You said: {req.message}",
            thread_id=req.thread_id,
            tool_calls=[],
        )

    @app.post("/chat/stream")
    async def chat_stream(req: ChatRequest) -> EventSourceResponse:
        """Streaming chat endpoint backed by the LangGraph supervisor.

        Yields SSE events following the Nanoclaw protocol: agent_think,
        agent_action, agent_observation, message_chunk, done.
        All events carry task_id (always "root" in Phase 1).
        """
        supervisor = get_supervisor()
        session_repo = get_session_repo()

        session_id = req.thread_id or str(uuid.uuid4())
        task_id = "root"

        # Create or resume session
        session = await session_repo.get(session_id)
        if session is None:
            session = await session_repo.create(
                ChatSession(id=session_id, created_at=time.time())
            )

        async def event_generator() -> AsyncGenerator[dict[str, str], None]:
            # Append user message to history
            await session_repo.append_message(
                session_id,
                ChatMessage(content=req.message, role="user"),
            )

            # Build initial state
            history = await session_repo.get_history(session_id)
            langchain_messages = [
                HumanMessage(content=m.content) if m.role == "user" or m.role == "system"
                else HumanMessage(content=m.content)
                for m in history
            ]

            initial_state = {
                "messages": langchain_messages,
                "session_id": session_id,
                "task_id": task_id,
                "session_repo": session_repo,
            }

            yield {
                "event": "task_status",
                "data": json.dumps(
                    {"task_id": task_id, "status": "RUNNING"}, ensure_ascii=False
                ),
            }

            try:
                # Stream graph execution events via astream_events
                async for event in supervisor.astream_events(
                    initial_state, version="v2"
                ):
                    kind = event.get("event")

                    if kind == "on_chat_model_start":
                        # Agent is about to think
                        pass

                    elif kind == "on_chat_model_stream":
                        chunk = event["data"]["chunk"]
                        if hasattr(chunk, "content") and chunk.content:
                            # Check for tool calls in streaming chunks
                            # DeepSeek may stream tool calls differently
                            if isinstance(chunk.content, str):
                                yield {
                                    "event": "agent_think",
                                    "data": json.dumps(
                                        {"content": chunk.content, "task_id": task_id},
                                        ensure_ascii=False,
                                    ),
                                }

                    elif kind == "on_chat_model_end":
                        # LLM turn complete — check if tool calls were made
                        output = event["data"]["output"]
                        if hasattr(output, "tool_calls") and output.tool_calls:
                            for tc in output.tool_calls:
                                tc_name = tc.get("name", "") if isinstance(tc, dict) else getattr(tc, "name", "")
                                tc_args = tc.get("args", {}) if isinstance(tc, dict) else getattr(tc, "args", {})
                                yield {
                                    "event": "agent_action",
                                    "data": json.dumps(
                                        {
                                            "tool": tc_name,
                                            "args": tc_args,
                                            "task_id": task_id,
                                        },
                                        ensure_ascii=False,
                                    ),
                                }
                        # Stream final content as message_chunk
                        if hasattr(output, "content") and output.content:
                            yield {
                                "event": "message_chunk",
                                "data": json.dumps(
                                    {"content": output.content, "task_id": task_id},
                                    ensure_ascii=False,
                                ),
                            }

                    elif kind == "on_tool_start":
                        # Tool execution started
                        pass

                    elif kind == "on_tool_end":
                        tool_name = event.get("name", "unknown")
                        tool_output = event["data"].get("output", "")
                        yield {
                            "event": "agent_observation",
                            "data": json.dumps(
                                {
                                    "tool": tool_name,
                                    "result": str(tool_output)[:2000],
                                    "task_id": task_id,
                                },
                                ensure_ascii=False,
                            ),
                        }

                # Persist assistant response to session history
                await session_repo.append_message(
                    session_id,
                    ChatMessage(content="", role="assistant", metadata={"task_id": task_id}),
                )

                yield {
                    "event": "done",
                    "data": json.dumps({"session_id": session_id}, ensure_ascii=False),
                }

            except Exception as exc:
                yield {
                    "event": "error",
                    "data": json.dumps(
                        {"message": str(exc), "task_id": task_id},
                        ensure_ascii=False,
                    ),
                }

        return EventSourceResponse(event_generator())

    return app
