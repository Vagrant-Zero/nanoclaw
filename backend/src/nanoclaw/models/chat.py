"""Chat-related data models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from nanoclaw.models.task import TaskPlan


@dataclass
class ChatMessage:
    """A single message in the chat history."""

    content: str
    role: str  # "user" | "assistant" | "system" | "tool"
    metadata: dict = field(default_factory=dict)
    """Extra data for future use (e.g., tool_call_id, timestamps)."""

    def to_dict(self) -> dict:
        return {
            "content": self.content,
            "role": self.role,
            "metadata": self.metadata.copy(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> ChatMessage:
        return cls(
            content=data["content"],
            role=data["role"],
            metadata=data.get("metadata", {}),
        )


@dataclass
class Step:
    """A single ReAct step record (think/action/observation).

    Used for both in-memory trace tracking and SSE event generation.
    Each step corresponds to one iteration of the ReAct loop.
    """

    type: Literal["think", "action", "observation"]
    content: str
    # Fields below are only populated when type is "action" or "observation"
    tool_name: str | None = None
    tool_args: dict | None = None
    tool_result: str | None = None
    timestamp: float = 0.0

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "content": self.content,
            "tool_name": self.tool_name,
            "tool_args": self.tool_args,
            "tool_result": self.tool_result,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Step:
        return cls(**data)


@dataclass
class Session:
    """Represents a conversation session.

    Created on first user message in a thread.
    Persisted (or checkpointed) for conversation continuity.
    """

    id: str
    created_at: float
    messages: list[ChatMessage] = field(default_factory=list)
    # Phase 2+: populated when a complex task plan is active
    active_plan: TaskPlan | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "created_at": self.created_at,
            "messages": [m.to_dict() for m in self.messages],
            "active_plan_id": self.active_plan.id if self.active_plan else None,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Session:
        messages = [ChatMessage.from_dict(m) for m in data.get("messages", [])]
        return cls(
            id=data["id"],
            created_at=data["created_at"],
            messages=messages,
        )
