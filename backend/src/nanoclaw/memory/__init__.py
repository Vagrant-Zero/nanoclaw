"""Memory subsystem — types, stores, and reflection engine."""
from __future__ import annotations

from nanoclaw.memory.reflection import ReflectionEngine
from nanoclaw.memory.store import (
    ChromaMemoryStore,
    JsonMemoryStore,
    MemoryStore,
)
from nanoclaw.memory.types import MemoryEntry, MemoryType


def create_memory_store(persist_dir: str) -> JsonMemoryStore:
    return JsonMemoryStore(persist_dir)


__all__ = [
    "ChromaMemoryStore",
    "JsonMemoryStore",
    "MemoryEntry",
    "MemoryStore",
    "MemoryType",
    "ReflectionEngine",
    "create_memory_store",
]
