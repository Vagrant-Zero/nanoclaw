"""Trajectory logger — writes execution traces to local JSONL files.

Each subtask's execution steps (think → action → observation) are streamed
as JSONL lines. The full trajectory is read by the failure-classification
LLM when a check fails.

Files are stored under:  .nanoclaw/trajectories/{session_id}/{subtask_id}.jsonl
"""

from __future__ import annotations

import json
import time
from pathlib import Path


class TrajectoryLogger:
    """Stream execution traces to local JSONL files.

    Uses synchronous file I/O wrapped in ``asyncio.to_thread`` to avoid
    adding aiofiles as a project dependency.
    """

    def __init__(self, base_dir: str = ".nanoclaw") -> None:
        self._base = Path(base_dir)

    def _path(self, session_id: str, subtask_id: str) -> Path:
        return self._base / "trajectories" / session_id / f"{subtask_id}.jsonl"

    async def append_step(self, session_id: str, subtask_id: str, step: dict) -> None:
        """Append one step to the trajectory file.

        Step is serialized as a JSON line and appended atomically.
        Parent directories are created on first write.
        """
        path = self._path(session_id, subtask_id)

        def _write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(step, ensure_ascii=False) + "\n")

        await asyncio.to_thread(_write)

    async def read_full(self, session_id: str, subtask_id: str) -> list[dict]:
        """Read the complete trajectory for a subtask.

        Returns an empty list if no trajectory file exists.
        """
        path = self._path(session_id, subtask_id)

        def _read() -> list[dict]:
            if not path.exists():
                return []
            text = path.read_text(encoding="utf-8")
            return [
                json.loads(line)
                for line in text.strip().split("\n")
                if line.strip()
            ]

        return await asyncio.to_thread(_read)

    async def cleanup(self, session_id: str, ttl_days: int = 30) -> None:
        """Remove trajectory files older than *ttl_days*."""
        dir_path = self._base / "trajectories" / session_id

        def _clean() -> None:
            if not dir_path.exists():
                return
            cutoff = time.time() - ttl_days * 86400
            for f in dir_path.iterdir():
                if f.is_file() and f.stat().st_mtime < cutoff:
                    f.unlink()

        await asyncio.to_thread(_clean)


# Need asyncio for to_thread
import asyncio  # noqa: E402  (import after class def to avoid top-level issues)
