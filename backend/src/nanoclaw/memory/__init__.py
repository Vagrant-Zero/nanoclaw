"""Memory subsystem — types, stores, and reflection engine.

Default backend is ``JsonMemoryStore`` (zero extra dependencies).
If ``chromadb`` is available, ``ChromaMemoryStore`` provides vector search.
"""
from __future__ import annotations

from nanoclaw.memory.store import (
    ChromaMemoryStore,
    JsonMemoryStore,
    MemoryStore,
)
from nanoclaw.memory.types import MemoryEntry, MemoryType


def create_memory_store(persist_dir: str) -> JsonMemoryStore:
    """Factory: construct the default memory store (JSONL-backed).

    Use ``ChromaMemoryStore`` directly if vector search is needed
    and ``chromadb`` is installed.
    """
    return JsonMemoryStore(persist_dir)


__all__ = [
    "ChromaMemoryStore",
    "JsonMemoryStore",
    "MemoryEntry",
    "MemoryStore",
    "MemoryType",
    "create_memory_store",
]
