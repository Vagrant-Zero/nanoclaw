"""FastAPI application factory — Phase 5 with PostgreSQL + Redis support."""

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
from nanoclaw.log_config import setup_logging
from sqlalchemy import text
from nanoclaw.context import ContextManager
from nanoclaw.dreaming import DreamingEngine, register_dreaming_tools
from nanoclaw.eval import EventLogger
from nanoclaw.memory import create_memory_store, ReflectionEngine
from nanoclaw.models.chat import ChatMessage, Session as ChatSession
from nanoclaw.scheduler import MemoryScheduledTaskRepo, Scheduler, ScheduledTask
from nanoclaw.scheduler.cron import parse_cron
from nanoclaw.server.deps import (
    create_queue,
    get_checkpointer,
    get_llm,
    get_scheduled_task_repo,
    get_session_repo,
    get_supervisor,
    get_task_repo,
    get_tool_registry,
    is_production,
)
from nanoclaw.storage.db import close_db, init_db
from nanoclaw.storage.redis_client import close_redis, get_redis
from nanoclaw.tools.registry import ToolRegistry

class ChatRequest(BaseModel):
    message: str
    thread_id: str | None = None

class HealthResponse(BaseModel):
    status: str
    version: str
    checks: dict[str, str] = {}

class ToolCallInfo(BaseModel):
    name: str
    args: dict
    result: str | None = None

class CreateScheduleRequest(BaseModel):
    description: str
    prompt: str
    schedule: str
    enabled: bool = True

class ChatResponse(BaseModel):
    response: str
    thread_id: str | None
    tool_calls: list[ToolCallInfo]

# ── Lifespan ─────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: initialize storage backends, create services.
    Shutdown: flush logs and close connections."""
    # Configure file-based logging before anything else
    setup_logging()
    # ── Phase 5: Initialize infrastructure if configured ──────
    # Fail fast: if DB/Redis is configured but unreachable, crash at startup.
    if settings.db_url:
        await init_db()
    if settings.redis_url:
        await get_redis()

    # ── Phase 3: EventLogger, MemoryStore, ReflectionEngine ────
    event_logger = EventLogger(settings.eval_dir)
    memory_store = create_memory_store(settings.chroma_persist_dir)
    llm = get_llm()
    reflection_engine = ReflectionEngine(memory_store, llm=llm)
    context_manager = ContextManager(memory_store)

    app.state.event_logger = event_logger
    app.state.memory_store = memory_store
    app.state.reflection_engine = reflection_engine
    app.state.context_manager = context_manager

    # ── Phase 4: Dreaming + Scheduler ──────────────────────────
    dreaming_registry = ToolRegistry()
    register_dreaming_tools(
        dreaming_registry, settings.eval_dir,
        memory_store, llm,
    )
    dreaming_engine = DreamingEngine(
        eval_logger=event_logger,
        memory_store=memory_store,
        session_repo=get_session_repo(),
        llm=llm,
        dreaming_tools=dreaming_registry,
        eval_base_dir=settings.eval_dir,
    )

    # Phase 5: Use PgScheduledTaskRepo if db_url is set, else memory
    sched_task_repo = get_scheduled_task_repo()
    scheduler = Scheduler(
        task_repo=sched_task_repo,
        session_repo=get_session_repo(),
        eval_logger=event_logger,
        llm=llm,
        tool_registry=get_tool_registry(),
        dreaming_engine=dreaming_engine,
        dreams_dir=settings.dreams_dir,
    )
    app.state.dreaming_engine = dreaming_engine
    app.state.scheduler = scheduler

    await scheduler.start()

    yield

    await scheduler.stop()
    await event_logger.close()

    # Phase 5: Close infrastructure connections
    await close_db()
    await close_redis()

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
        """Health check endpoint with per-service status.

        Returns connectivity status for PostgreSQL, Redis, and Chroma
        (not yet integrated). Each service is reported as:
          - ``ok`` — reachable and responsive
          - ``unreachable`` — configured but not responding
          - ``disabled`` — not configured / not integrated
        """
        checks: dict[str, str] = {}

        # PostgreSQL
        if settings.db_url:
            try:
                from nanoclaw.storage.db import get_session
                async with get_session() as conn:
                    await conn.execute(text("SELECT 1"))
                checks["postgres"] = "ok"
            except RuntimeError:
                checks["postgres"] = "disabled"
            except Exception:
                checks["postgres"] = "unreachable"
        else:
            checks["postgres"] = "disabled"

        # Redis
        if settings.redis_url:
            try:
                redis = await get_redis()
                await redis.ping()
                checks["redis"] = "ok"
            except RuntimeError:
                checks["redis"] = "disabled"
            except Exception:
                checks["redis"] = "unreachable"
        else:
            checks["redis"] = "disabled"

        # Chroma — not yet integrated, always disabled
        checks["chroma"] = "disabled"

        # Overall status: "ok" if all enabled services are healthy
        all_ok = all(v in ("ok", "disabled") for v in checks.values())
        overall = "ok" if all_ok else "degraded"

        return HealthResponse(status=overall, version="0.1.0", checks=checks)

    # ── Chat endpoints ──

    @app.post("/chat", response_model=ChatResponse)
    async def chat(req: ChatRequest) -> ChatResponse:
        """Synchronous chat backed by the Phase 2 Supervisor graph."""
        supervisor = get_supervisor()
        session_repo = get_session_repo()
        tool_registry = get_tool_registry()
        llm = get_llm()

        session_id = req.thread_id or str(uuid.uuid4())
        session = await session_repo.get(session_id)
        if session is None:
            session = await session_repo.create(
                ChatSession(id=session_id, created_at=time.time())
            )

        # Per-request queue: MemoryQueue or RedisQueue based on config
        task_queue = create_queue(session_id)

        from nanoclaw.agent.nodes.react_agent import create_react_agent
        from nanoclaw.agent.worker_pool import WorkerPool

        worker_agent = create_react_agent(llm, tool_registry)
        worker_pool = WorkerPool(
            task_queue=task_queue, react_agent=worker_agent, llm=llm,
        )

        state: dict = {
            "messages": [HumanMessage(content=req.message)],
            "session_id": session_id, "task_id": "root",
            "session_repo": session_repo,
            "tool_registry": tool_registry, "task_queue": task_queue,
            "plan": None, "worker_pool": worker_pool,
            "worker_results": None, "errors": [],
            "checker_feedback": None, "iteration_budget": None,
            "trajectory_logger": None,
            "event_logger": getattr(app.state, "event_logger", None),
            "reflection_engine": getattr(app.state, "reflection_engine", None),
        }

        try:
            result = await supervisor.ainvoke(state)
        finally:
            await worker_pool.stop()

        msgs = result.get("messages", [])
        final = msgs[-1] if msgs else None
        response_text = final.content if final else ""

        tool_calls_info: list[ToolCallInfo] = []
        for m in msgs:
            if hasattr(m, "tool_calls") and m.tool_calls:
                for tc in m.tool_calls:
                    n = tc.get("name", "") if isinstance(tc, dict) else getattr(tc, "name", "")
                    a = tc.get("args", {}) if isinstance(tc, dict) else getattr(tc, "args", {})
                    tool_calls_info.append(ToolCallInfo(name=n, args=a, result=None))

        return ChatResponse(
            response=response_text,
            thread_id=session_id,
            tool_calls=tool_calls_info,
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
        task_queue = create_queue(session_id)
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
                    result = await supervisor.ainvoke(initial_state)
                    # Emit the final response as message_chunk so the
                    # StreamingChat component receives it and onDone
                    # has the full text.
                    msgs = result.get("messages", [])
                    if msgs:
                        final = msgs[-1]
                        if hasattr(final, "content") and final.content:
                            await sse_queue.put({
                                "event": "message_chunk",
                                "data": {"content": final.content},
                            })
                except asyncio.CancelledError:
                    pass
                except Exception as exc:
                    try:
                        await sse_queue.put({
                            "event": "error",
                            "data": {"message": str(exc)[:500], "task_id": "root"},
                        })
                    except Exception:
                        logger.warning('SSE queue put error', exc_info=True)
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

    # ── Phase 4: Dreaming trigger ──────────────────────────────────

    @app.post("/dream")
    async def trigger_dreaming(date: str | None = None) -> dict:
        """Manually trigger the daily dreaming process."""
        engine = getattr(app.state, "dreaming_engine", None)
        if engine is None:
            raise HTTPException(
                status_code=503,
                detail="Dreaming engine not available",
            )
        try:
            summary = await engine.run_dreaming(date)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))
        return {"status": "ok", "date": summary["date"], "summary": summary}

    # ── Phase 4: Scheduled task CRUD ───────────────────────────────

    @app.get("/schedules")
    async def list_schedules() -> dict:
        sched = getattr(app.state, "scheduler", None)
        tasks = await sched.task_repo.list_all() if sched else []
        return {
            "tasks": [
                {
                    "id": t.id, "description": t.description,
                    "prompt": t.prompt, "schedule": t.schedule,
                    "enabled": t.enabled, "last_run": t.last_run,
                    "created_at": t.created_at,
                }
                for t in tasks
            ]
        }

    @app.post("/schedules")
    async def create_schedule(req: CreateScheduleRequest) -> dict:
        try:
            parse_cron(req.schedule)
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail=f"Invalid cron expression: {exc}",
            )
        sched = getattr(app.state, "scheduler", None)
        if sched is None:
            raise HTTPException(status_code=503, detail="Scheduler not available")
        task = ScheduledTask(
            description=req.description,
            prompt=req.prompt,
            schedule=req.schedule,
            enabled=req.enabled,
        )
        created = await sched.task_repo.create(task)
        return {
            "status": "ok",
            "task": {
                "id": created.id, "description": created.description,
                "schedule": created.schedule, "enabled": created.enabled,
            },
        }

    @app.delete("/schedules/{task_id}")
    async def delete_schedule(task_id: str) -> dict:
        sched = getattr(app.state, "scheduler", None)
        if sched is None:
            raise HTTPException(status_code=503, detail="Scheduler not available")
        await sched.task_repo.delete(task_id)
        return {"status": "ok"}

    @app.patch("/schedules/{task_id}/toggle")
    async def toggle_schedule(task_id: str) -> dict:
        sched = getattr(app.state, "scheduler", None)
        if sched is None:
            raise HTTPException(status_code=503, detail="Scheduler not available")
        task = await sched.task_repo.get(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Schedule not found")
        updated = await sched.task_repo.update(
            task_id, {"enabled": not task.enabled}
        )
        return {
            "status": "ok",
            "task": {
                "id": updated.id, "description": updated.description,
                "schedule": updated.schedule, "enabled": updated.enabled,
            },
        }

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
