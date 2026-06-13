"""EventLogger — asynchronous batched JSONL writer.

Events are queued per-session and written in batches (up to 50 events
or every 1 second).  One background writer coroutine per active session
drains the queue and appends to ``{base_dir}/{session_id}/events.jsonl``.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any


class EventLogger:
    """Asynchronous event logger with batched JSONL writing.

    Usage::

        logger = EventLogger(".nanoclaw/eval")
        await logger.log_event("sess_1", "llm_call", {"model": "gpt-4"})
        ...
        await logger.close()  # flush and stop all writers
    """

    def __init__(self, base_dir: str | Path) -> None:
        self._base = Path(base_dir)
        self._queues: dict[str, asyncio.Queue] = {}
        self._writers: dict[str, asyncio.Task] = {}
        self._closed = False

    # ── Public API ──

    async def log_event(
        self,
        session_id: str,
        event_type: str,
        data: dict[str, Any],
    ) -> None:
        """Queue an event for batch writing.

        Creates a writer task for the session on first call.
        """
        if self._closed:
            return
        if session_id not in self._queues:
            self._queues[session_id] = asyncio.Queue()
            self._writers[session_id] = asyncio.create_task(
                self._writer_loop(session_id),
            )
        await self._queues[session_id].put((event_type, data, time.time()))

    async def flush_session(self, session_id: str) -> None:
        """Force-flush one session's event queue."""
        queue = self._queues.get(session_id)
        if queue is None:
            return
        events = self._drain_queue(queue)
        if events:
            await self._write_events(session_id, events)

    async def close(self) -> None:
        """Cancel all writers and flush remaining events."""
        self._closed = True
        for writer in list(self._writers.values()):
            writer.cancel()
        if self._writers:
            await asyncio.gather(
                *self._writers.values(), return_exceptions=True
            )
            self._writers.clear()
        # Drain queues whose writers already finished
        for sid, queue in self._queues.items():
            events = self._drain_queue(queue)
            if events:
                await self._write_events(sid, events)
        self._queues.clear()

    # ── Writer loop ──

    async def _writer_loop(self, session_id: str) -> None:
        """Background writer: collect events in batches, write to JSONL."""
        queue = self._queues[session_id]
        try:
            while not self._closed:
                # Wait up to 1 second for the first event
                try:
                    first = await asyncio.wait_for(queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue

                # Drain available events (up to 50 per batch)
                batch: list[tuple] = [first]
                while len(batch) < 50:
                    try:
                        batch.append(queue.get_nowait())
                    except asyncio.QueueEmpty:
                        break

                await self._write_events(session_id, batch)

        except asyncio.CancelledError:
            # Flush remaining events before exiting
            remaining = self._drain_queue(queue)
            if remaining:
                await self._write_events(session_id, remaining)

    # ── File I/O ──

    async def _write_events(
        self,
        session_id: str,
        events: list[tuple[str, dict, float]],
    ) -> None:
        """Write a batch of events to the JSONL file (runs in thread)."""
        dir_path = self._base / session_id
        file_path = dir_path / "events.jsonl"

        def _write() -> None:
            dir_path.mkdir(parents=True, exist_ok=True)
            with open(file_path, "a", encoding="utf-8") as f:
                for event_type, data, ts in events:
                    f.write(
                        json.dumps(
                            {"type": event_type, "data": data, "timestamp": ts},
                            ensure_ascii=False,
                        )
                        + "\n"
                    )

        await asyncio.to_thread(_write)

    # ── Helpers ──

    @staticmethod
    def _drain_queue(queue: asyncio.Queue) -> list:
        """Non-blocking drain: return all items currently in the queue."""
        items: list = []
        while not queue.empty():
            try:
                items.append(queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        return items
