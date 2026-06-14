"""Checkpointer abstraction and local file implementation.

Checkpointer persists LangGraph graph state snapshots for pause/resume
and crash recovery. Each session has at most one active checkpoint
(its latest graph state).
"""

from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from pathlib import Path

from nanoclaw.models.task import CheckpointState


class Checkpointer(ABC):
    """Abstract checkpoint storage.

    Implementations:
      - LocalFileCheckpointer (JSON files on disk)
      - PgCheckpointer (PostgreSQL via sessions.serialized_state, Phase 5+)
    """

    @abstractmethod
    async def save(self, session_id: str, state: CheckpointState) -> None:
        """Persist a checkpoint for a session."""

    @abstractmethod
    async def load(self, session_id: str) -> CheckpointState | None:
        """Load the latest checkpoint for a session, or None."""

    @abstractmethod
    async def list_sessions(self) -> list[str]:
        """Return session IDs that have active checkpoints."""


class LocalFileCheckpointer(Checkpointer):
    """File-based checkpoint storage.

    Writes each checkpoint as a JSON file at:
      ``{checkpoint_dir}/{session_id}.json``

    Suitable for development; replaced by PgCheckpointer in Phase 5+.
    """

    def __init__(self, checkpoint_dir: str = ".nanoclaw/checkpoints") -> None:
        self._checkpoint_dir = Path(checkpoint_dir)
        self._checkpoint_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, session_id: str) -> Path:
        return self._checkpoint_dir / f"{session_id}.json"

    async def save(self, session_id: str, state: CheckpointState) -> None:
        data = state.to_dict()
        self._path(session_id).write_text(json.dumps(data, ensure_ascii=False))

    async def load(self, session_id: str) -> CheckpointState | None:
        path = self._path(session_id)
        if not path.exists():
            return None
        data = json.loads(path.read_text())
        return CheckpointState.from_dict(data)

    async def list_sessions(self) -> list[str]:
        if not self._checkpoint_dir.exists():
            return []
        return [
            f.stem for f in self._checkpoint_dir.iterdir()
            if f.suffix == ".json"
        ]
