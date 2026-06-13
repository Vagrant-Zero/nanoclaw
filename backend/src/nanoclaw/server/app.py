"""FastAPI application factory."""

from __future__ import annotations

import asyncio
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
        """Streaming chat endpoint backed by the Phase 2 Supervisor graph.

        Uses an ``asyncio.Queue`` bridge to collect SSE events from the
        WorkerPool and the graph execution, then yields them to the client.

        SSE protocol events: task_status, agent_think, agent_action,
        agent_observation, agent_plan, check_result, message_chunk,
        iteration_exhausted, done, error.
        """
        supervisor = get_supervisor()
        session_repo = get_session_repo()
        tool_registry = get_tool_registry()
        llm = get_llm()

        session_id = req.thread_id or str(uuid.uuid4())

        # ── SSE event queue (bridge between graph/workers and SSE stream) ──
        sse_queue: asyncio.Queue[dict] = asyncio.Queue()

        async def sse_callback(event: str, data: dict) -> None:
            """Put an event into the SSE queue (non-blocking wrapper)."""
            await sse_queue.put({"event": event, "data": data})

        # ── Per-request task queue and worker pool ──
        from nanoclaw.agent.nodes.react_agent import create_react_agent
        from nanoclaw.agent.worker_pool import WorkerPool
        from nanoclaw.storage.task_queue import MemoryQueue

        task_queue = MemoryQueue()
        worker_react_agent = create_react_agent(
            llm, tool_registry, sse_callback=sse_callback,
        )
        worker_pool = WorkerPool(
            task_queue=task_queue,
            react_agent=worker_react_agent,
            llm=llm,
            sse_callback=sse_callback,
        )

        # Create or resume session
        session = await session_repo.get(session_id)
        if session is None:
            session = await session_repo.create(
                ChatSession(id=session_id, created_at=time.time())
            )

        async def event_generator() -> AsyncGenerator[dict[str, str], None]:
            # Append user message
            await session_repo.append_message(
                session_id,
                ChatMessage(content=req.message, role="user"),
            )

            # Initial task_status
            yield {
                "event": "task_status",
                "data": json.dumps(
                    {"task_id": "root", "status": "RUNNING"},
                    ensure_ascii=False,
                ),
            }

            # ── Build initial state for SupervisorState ──
            initial_state: dict = {
                "messages": [HumanMessage(content=req.message)],
                "session_id": session_id,
                "task_id": "root",
                "session_repo": session_repo,
                "tool_registry": tool_registry,
                "task_queue": task_queue,
                "plan": None,
                "worker_pool": worker_pool,
                "worker_results": None,
                "errors": [],
                "checker_feedback": None,
                "iteration_budget": None,
                "trajectory_logger": None,
            }

            # ── Background graph execution ──
            async def run_graph() -> None:
                try:
                    await supervisor.ainvoke(initial_state)
                except asyncio.CancelledError:
                    pass  # Client disconnected
                except Exception as exc:
                    try:
                        await sse_queue.put({
                            "event": "error",
                            "data": {
                                "message": str(exc)[:500],
                                "task_id": "root",
                            },
                        })
                    except Exception:
                        pass
                finally:
                    try:
                        await worker_pool.stop()
                    except Exception:
                        pass
                    await sse_queue.put({
                        "event": "done",
                        "data": {"session_id": session_id},
                    })

            graph_task = asyncio.create_task(run_graph())

            # ── Event forwarding loop ──
            try:
                while True:
                    event_data = await sse_queue.get()
                    yield {
                        "event": event_data["event"],
                        "data": json.dumps(
                            event_data["data"], ensure_ascii=False
                        ),
                    }
                    if event_data["event"] == "done":
                        break
            except asyncio.CancelledError:
                pass
            finally:
                graph_task.cancel()
                try:
                    await graph_task
                except (asyncio.CancelledError, Exception):
                    pass

        return EventSourceResponse(event_generator())

    return app
