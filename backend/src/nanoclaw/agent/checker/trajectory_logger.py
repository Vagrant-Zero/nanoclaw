"""Trajectory logger — writes execution traces to local JSONL files.

Stub for Phase 2, Task 5.
"""

from __future__ import annotations

from pathlib import Path


class TrajectoryLogger:
    """Stream execution traces to local JSONL files.

    Appends one event per line: {"step": N, "type": "think"|"action"|"observation", ...}
    Files stored under: .nanoclaw/trajectories/{session_id}/{subtask_id}.jsonl
    """

    def __init__(self, base_dir: str = ".nanoclaw") -> None:
        self.base_dir = Path(base_dir)

    async def append_step(self, session_id: str, subtask_id: str, step: dict) -> None:
        """Append one step to the trajectory file."""
        pass  # Stub

    async def read_full(self, session_id: str, subtask_id: str) -> list[dict]:
        """Read full trajectory."""
        return []  # Stub

    async def cleanup(self, session_id: str, ttl_days: int = 30) -> None:
        """Clean up expired trajectory files."""
        pass  # Stub
