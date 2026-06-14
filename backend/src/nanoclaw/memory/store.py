"""Memory store — abstract base class and implementations.

Implementations:
- ``JsonMemoryStore`` — zero-dependency JSONL backend, suitable for
  development and personal use.  No vector search (tag + keyword only).
- ``ChromaMemoryStore`` — ChromaDB-backed store with vector similarity
  search.  Requires ``chromadb`` (optional, heavy dependency).
"""

from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nanoclaw.memory.types import MemoryEntry


# ── Abstract base ───────────────────────────────────────────────────


class MemoryStore(ABC):
    """Abstract interface for persistent memory storage."""

    @abstractmethod
    async def save(self, entry: MemoryEntry) -> None:
        """Persist a memory entry."""

    @abstractmethod
    async def search(
        self,
        query: str,
        tags: list[str] | None = None,
        top_k: int = 5,
    ) -> list[MemoryEntry]:
        """Search memories by semantic similarity and optional tag filter."""

    @abstractmethod
    async def confirm(self, entry_id: str) -> MemoryEntry | None:
        """Mark an entry as confirmed (user-approved)."""

    @abstractmethod
    async def delete(self, entry_id: str) -> bool:
        """Delete a memory entry. Returns True if it existed."""


# ── JSONL-based store (zero external dependencies) ──────────────────


class JsonMemoryStore(MemoryStore):
    """In-memory dict + JSONL persistence.

    Entries are kept in memory for fast lookups and appended to a JSONL
    file for durability.  On startup the file is replayed to rebuild the
    in-memory index.  Thread-safe via ``asyncio.Lock``.

    Search is tag + keyword (no vector similarity).  Sufficient for
    development and single-user scenarios with modest memory counts.
    """

    def __init__(self, persist_directory: str) -> None:
        self._dir = Path(persist_directory)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path = self._dir / "memories.jsonl"
        self._entries: dict[str, MemoryEntry] = {}
        self._lock = __import__("asyncio").Lock()

        # Rebuild in-memory index from file
        self._load()

    # ── Public API ──

    async def save(self, entry: MemoryEntry) -> None:
        async with self._lock:
            self._entries[entry.id] = entry
            await self._flush()

    async def search(
        self,
        query: str,
        tags: list[str] | None = None,
        top_k: int = 5,
    ) -> list[MemoryEntry]:
        q = query.lower()
        tag_set = set(tags or [])

        matched: list[MemoryEntry] = []
        async with self._lock:
            for e in self._entries.values():
                if not e.confirmed:
                    continue  # Only search confirmed memories
                if tag_set and not tag_set.intersection(e.tags):
                    continue  # Tag filter
                if q and q not in e.content.lower():
                    continue  # Keyword filter
                matched.append(e)

        # Sort by confidence descending, then recency
        matched.sort(key=lambda x: (-x.confidence, -x.created_at))
        return matched[:top_k]

    async def confirm(self, entry_id: str) -> MemoryEntry | None:
        async with self._lock:
            entry = self._entries.get(entry_id)
            if entry is None:
                return None
            updated = replace(entry, confirmed=True)
            self._entries[entry_id] = updated
            await self._flush()
            return updated

    async def delete(self, entry_id: str) -> bool:
        async with self._lock:
            if entry_id not in self._entries:
                return False
            del self._entries[entry_id]
            await self._flush()
            return True

    # ── Persistence ──

    def _load(self) -> None:
        """Load all entries from JSONL into memory."""
        if not self._path.exists():
            return
        with open(self._path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                data = json.loads(line)
                entry = _dict_to_entry(data)
                self._entries[entry.id] = entry

    async def _flush(self) -> None:
        """Write all entries back to the JSONL file."""
        path = self._path

        def _write():
            with open(path, "w", encoding="utf-8") as f:
                for e in self._entries.values():
                    f.write(json.dumps(_entry_to_dict(e), ensure_ascii=False) + "\n")

        await __import__("asyncio").to_thread(_write)

    async def count(self) -> int:
        """Return total number of stored entries (for introspection)."""
        async with self._lock:
            return len(self._entries)

    async def list_unconfirmed(self) -> list[MemoryEntry]:
        """Return all unconfirmed entries (for TUI display)."""
        async with self._lock:
            return [e for e in self._entries.values() if not e.confirmed]


# ── ChromaDB-backed store (optional heavy dependency) ────────────────


class ChromaMemoryStore(MemoryStore):
    """Memory store backed by ChromaDB persistent client.

    Requires ``chromadb``.  Falls back to simple tag+keyword matching
    if vector search is unavailable.
    """

    def __init__(self, persist_directory: str) -> None:  # noqa: C901
        try:
            import chromadb  # noqa: F811
        except ImportError as exc:
            raise ImportError(
                "chromadb is not installed.  Use JsonMemoryStore instead, or "
                "run: uv pip install chromadb"
            ) from exc

        self._client = chromadb.PersistentClient(path=persist_directory)
        try:
            self._collection = self._client.create_collection(
                name="memories",
                metadata={"hnsw:space": "cosine"},
            )
        except ValueError:
            self._collection = self._client.get_collection(name="memories")

    # ── Public API ──

    async def save(self, entry: MemoryEntry) -> None:
        self._add_entry(entry)

    async def search(
        self,
        query: str,
        tags: list[str] | None = None,
        top_k: int = 5,
    ) -> list[MemoryEntry]:
        where = self._build_tag_filter(tags)

        results = self._collection.query(
            query_texts=[query],
            n_results=top_k,
            where=where,
        )
        entries = self._parse_results(results)

        # Fallback: if tag filter returned too few results, merge with
        # unfiltered results (deduplicate by ID).
        if len(entries) < top_k and where is not None:
            fallback = self._collection.query(
                query_texts=[query],
                n_results=top_k,
                where=None,
            )
            seen = {e.id for e in entries}
            for e in self._parse_results(fallback):
                if e.id not in seen and len(entries) < top_k:
                    entries.append(e)
                    seen.add(e.id)

        return entries

    async def confirm(self, entry_id: str) -> MemoryEntry | None:
        result = self._collection.get(ids=[entry_id])
        if not result["ids"]:
            return None
        entry = self._parse_single(result)
        if entry is None:
            return None
        updated = replace(entry, confirmed=True)
        self._collection.delete(ids=[entry_id])
        self._add_entry(updated)
        return updated

    async def delete(self, entry_id: str) -> bool:
        result = self._collection.get(ids=[entry_id])
        if not result["ids"]:
            return False
        self._collection.delete(ids=[entry_id])
        return True

    # ── Internal helpers ──

    def _add_entry(self, entry: MemoryEntry) -> None:
        meta: dict[str, str | float | int] = {
            "type": entry.type.value,
            "_tags": ",".join(entry.tags),
            "source": entry.source,
            "confidence": entry.confidence,
            "created_at": entry.created_at,
            "confirmed": int(entry.confirmed),
        }
        kwargs: dict = {
            "ids": [entry.id],
            "documents": [entry.content],
            "metadatas": [meta],
        }
        if entry.embedding is not None:
            kwargs["embeddings"] = [entry.embedding]
        self._collection.add(**kwargs)

    def _build_tag_filter(self, tags: list[str] | None) -> dict | None:
        if not tags:
            return None
        if len(tags) == 1:
            return {"_tags": {"$contains": tags[0]}}
        return {"$and": [{"_tags": {"$contains": t}} for t in tags]}

    def _parse_results(self, raw: dict) -> list:
        from nanoclaw.memory.types import MemoryEntry as ME, MemoryType as MT

        entries: list = []
        for i in range(len(raw["ids"][0])):
            eid = raw["ids"][0][i]
            doc = raw["documents"][0][i] if raw["documents"] else ""
            meta = raw["metadatas"][0][i] if raw["metadatas"] else {}
            raw_tags = (meta.get("_tags") or "").split(",")
            tags = [t for t in raw_tags if t]
            entries.append(ME(
                id=eid,
                type=MT(meta.get("type", MT.REFLECTION.value)),
                tags=tags,
                content=doc or "",
                source=meta.get("source", ""),
                confidence=float(meta.get("confidence", 0.0)),
                created_at=float(meta.get("created_at", 0.0)),
                confirmed=bool(int(meta.get("confirmed", 0))),
            ))
        return entries

    def _parse_single(self, raw: dict):
        from nanoclaw.memory.types import MemoryEntry as ME, MemoryType as MT

        if not raw["ids"]:
            return None
        meta = raw["metadatas"][0] if raw.get("metadatas") else {}
        raw_tags = (meta.get("_tags") or "").split(",")
        tags = [t for t in raw_tags if t]
        return ME(
            id=raw["ids"][0],
            type=MT(meta.get("type", MT.REFLECTION.value)),
            tags=tags,
            content=raw["documents"][0] if raw.get("documents") else "",
            source=meta.get("source", ""),
            confidence=float(meta.get("confidence", 0.0)),
            created_at=float(meta.get("created_at", 0.0)),
            confirmed=bool(int(meta.get("confirmed", 0))),
        )


# ── Serialisation helpers ────────────────────────────────────────────


def _entry_to_dict(entry: MemoryEntry) -> dict:
    """Serialise a MemoryEntry to a JSON-compatible dict."""
    return {
        "id": entry.id,
        "type": entry.type.value,
        "tags": entry.tags,
        "content": entry.content,
        "embedding": entry.embedding,
        "source": entry.source,
        "confidence": entry.confidence,
        "created_at": entry.created_at,
        "confirmed": entry.confirmed,
    }


def _dict_to_entry(data: dict):
    """Deserialise a dict back into a MemoryEntry."""
    from nanoclaw.memory.types import MemoryEntry as ME
    from nanoclaw.memory.types import MemoryType

    return ME(
        id=data["id"],
        type=MemoryType(data.get("type", MemoryType.REFLECTION.value)),
        tags=data.get("tags", []),
        content=data.get("content", ""),
        embedding=data.get("embedding"),
        source=data.get("source", ""),
        confidence=float(data.get("confidence", 0.0)),
        created_at=float(data.get("created_at", 0.0)),
        confirmed=bool(data.get("confirmed", False)),
    )
