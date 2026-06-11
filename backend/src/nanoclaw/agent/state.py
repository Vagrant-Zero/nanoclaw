"""Agent state definition using TypedDict."""

from __future__ import annotations

from typing import Annotated, Sequence, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages

from nanoclaw.storage.session_repo import SessionRepository


class AgentState(TypedDict):
    """State passed between nodes in the agent graph.

    messages:     LangChain message history (auto-merged by add_messages reducer)
    session_id:   Current conversation session ID
    task_id:      "root" (simple path) or "task_xxx" (subtask, Phase 2+)
    session_repo: Reference to session storage for history persistence
    """

    messages: Annotated[Sequence[AnyMessage], add_messages]
    session_id: str | None
    task_id: str | None
    session_repo: SessionRepository | None
