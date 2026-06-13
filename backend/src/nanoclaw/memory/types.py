"""Memory data models — MemoryType enum and MemoryEntry dataclass.

These types are used across the Memory store, Reflection engine, and
ContextManager. Immutable (frozen) dataclass for thread safety.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum


class MemoryType(str, Enum):
    """Categorization of a memory entry.

    Values:
        user_profile: User preferences, language/framework choices, typical patterns.
        skill:        Verified tool-combination patterns and workflow templates.
        semantic:     Project knowledge, domain understanding, learned facts.
        reflection:   Post-task insight draft (unconfirmed until user approves).
    """

    USER_PROFILE = "user_profile"
    SKILL = "skill"
    SEMANTIC = "semantic"
    REFLECTION = "reflection"


@dataclass(frozen=True)
class MemoryEntry:
    """A single persisted memory entry.

    All fields are immutable after creation. To modify (e.g. confirm),
    read the existing entry, create a new instance with ``dataclasses.replace``,
    delete the old one, and save the new one.
    """

    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    type: MemoryType = MemoryType.REFLECTION
    tags: list[str] = field(default_factory=list)
    content: str = ""
    embedding: list[float] | None = None
    source: str = ""  # session_id or task_id that generated this
    confidence: float = 0.0  # [0, 1]
    created_at: float = field(default_factory=time.time)
    confirmed: bool = False
