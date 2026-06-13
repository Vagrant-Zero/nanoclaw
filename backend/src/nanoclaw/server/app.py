"""FastAPI application factory — Phase 3 with EventLogger, memory, and Reflection."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, HTTPException
from langchain_core.messages import HumanMessage
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse
from starlette.responses import Response

from nanoclaw.config import settings
from nanoclaw.context import ContextManager
from nanoclaw.eval import EventLogger
from nanoclaw.memory import create_memory_store, ReflectionEngine
from nanoclaw.models.chat import ChatMessage, Session as ChatSession
from nanoclaw.server.deps import get_llm, get_session_repo, get_supervisor, get_tool_registry


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


# ── Lifespan ─────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: create EventLogger, MemoryStore, ReflectionEngine.
    Shutdown: flush and close EventLogger."""
    event_logger = EventLogger(settings.eval_dir)
    memory_store = create_memory_store(settings.chroma_persist_dir)
    llm = get_llm()
    reflection_engine = ReflectionEngine(memory_store, llm=llm)
    context_manager = ContextManager(memory_store)

    app.state.event_logger = event_logger
    app.state.memory_store = memory_store
    app.state.reflection_engine = reflection_engine
    app.state.context_manager = context_manager

    yield

    await event_logger.close()


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""

    app = FastAPI(
        title="Nanoclaw",
        version="0.1.0",
        description="Personal AI assistant API",
        lifespan=lifespan,
    )

    # ── Health ──

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(status="ok", version="0.1.0")

    # ── Chat endpoints ──

    @app.post("/chat", response_model=ChatResponse)
    async def chat(req: ChatRequest) -> ChatResponse:
        return ChatResponse(
            response=f"You said: {req.message}",
            thread_id=req.thread_id,
            tool_calls=[],
        )

    @app.post("/chat/stream")
    async def chat_stream(req: ChatRequest) -> EventSourceResponse:
        """Streaming chat backed by the Phase 2 Supervisor graph."""

        supervisor = get_supervisor()
        session_repo = get_session_repo()
        tool_registry = get_tool_registry()
        llm = get_llm()

        session_id = req.thread_id or str(uuid.uuid4())

        # SSE event queue bridge
        sse_queue: asyncio.Queue[dict] = asyncio.Queue()

        async def sse_callback(event: str, data: dict) -> None:
            await sse_queue.put({"event": event, "data": data})

        # Per-request task queue and worker pool
        from nanoclaw.agent.nodes.react_agent import create_react_agent
        from nanoclaw.agent.worker_pool import WorkerPool
        from nanoclaw.storage.task_queue import MemoryQueue

        task_queue = MemoryQueue()
        worker_react_agent = create_react_agent(
            llm, tool_registry, sse_callback=sse_callback,
            context_manager=getattr(app.state, "context_manager", None),
            event_logger=getattr(app.state, "event_logger", None),
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
            await session_repo.append_message(
                session_id,
                ChatMessage(content=req.message, role="user"),
            )

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
                # Phase 3 (safer access for test environments)
                "event_logger": getattr(app.state, "event_logger", None),
                "reflection_engine": getattr(app.state, "reflection_engine", None),
            }

            # Background graph execution
            async def run_graph() -> None:
                try:
                    await supervisor.ainvoke(initial_state)
                except asyncio.CancelledError:
                    pass
                except Exception as exc:
                    try:
                        await sse_queue.put({
                            "event": "error",
                            "data": {"message": str(exc)[:500], "task_id": "root"},
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

            try:
                while True:
                    event_data = await sse_queue.get()
                    yield {
                        "event": event_data["event"],
                        "data": json.dumps(event_data["data"], ensure_ascii=False),
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

    # ── Memory endpoints ────────────────────────────────────────────

    @app.post("/memories/{entry_id}/confirm")
    async def confirm_memory(entry_id: str) -> dict:
        store = app.state.memory_store
        entry = await store.confirm(entry_id)
        if entry is None:
            raise HTTPException(status_code=404, detail="Memory entry not found")
        # Log feedback
        el = app.state.event_logger
        if el is not None:
            await el.log_event("api", "user_feedback", {
                "feedback_type": "confirm",
                "content": entry.content[:200],
                "memory_entry_id": entry_id,
            })
        return {"id": entry.id, "confirmed": entry.confirmed}

    @app.post("/memories/{entry_id}/reject")
    async def reject_memory(entry_id: str) -> Response:
        store = app.state.memory_store
        deleted = await store.delete(entry_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Memory entry not found")
        return Response(status_code=204)

    @app.get("/memories")
    async def list_memories(type: str | None = None, limit: int = 20) -> list[dict]:
        store = app.state.memory_store
        entries = await store.list_unconfirmed()
        if type:
            entries = [e for e in entries if e.type.value == type]
        return [
            {"id": e.id, "type": e.type.value, "summary": e.content[:200],
             "created_at": e.created_at}
            for e in entries[:limit]
        ]

    return app
