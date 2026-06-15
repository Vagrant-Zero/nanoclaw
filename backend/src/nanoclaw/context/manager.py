"""ContextManager — assembles LLM prompts from memory, session, and task state.

The build order is::

    1. System prompt         (role definition)
    2. User profile          (from MemoryStore, ``user_profile`` tag)
    3. Skill injections      (from MemoryStore, ``skill`` tag, if active subtask)
    4. Thread context        (session message history)
    5. Active task state     (subtask status and trace, if active subtask)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

if TYPE_CHECKING:
    from nanoclaw.memory.store import MemoryStore
    from nanoclaw.models.chat import ChatMessage
DEFAULT_SYSTEM_PROMPT = (
    "You are Nanoclaw, a personal AI assistant. "
    "Help the user accomplish their tasks by reasoning step by step and "
    "using the available tools when appropriate. Be concise and precise."
)

@dataclass
class SessionContext:
    """Minimal session info required by the ContextManager.

    This avoids coupling to the full ``Session`` model from ``models.chat``.
    """

    id: str
    messages: list[ChatMessage] = field(default_factory=list)

class ContextManager:
    """Assembles LLM prompts from memory, session history, and task state.

    Usage::

        cm = ContextManager(memory_store=store)
        prompt = await cm.build_prompt(
            SessionContext(id="s1", messages=[...]),
            active_subtask=current_subtask,
        )
        response = await llm.ainvoke(prompt)
    """

    def __init__(self, memory_store: MemoryStore | None = None) -> None:
        self._memory_store = memory_store

    async def build_prompt(
        self,
        session: SessionContext,
        active_subtask: object | None = None,
    ) -> list:
        """Build a message list for the LLM.

        Args:
            session: The current conversation session.
            active_subtask: Optional Subtask object for injecting task state.

        Returns:
            A list of ``langchain_core.messages.BaseMessage`` suitable for
            ``llm.ainvoke()``.
        """
        messages: list = []

        # 1. System prompt
        messages.append(SystemMessage(content=DEFAULT_SYSTEM_PROMPT))

        # 2. User profile from memory
        if self._memory_store is not None:
            await self._inject_profile(messages)

        # 3. Skill injections (only when an active subtask is running)
        if active_subtask is not None and self._memory_store is not None:
            await self._inject_skills(messages, active_subtask)

        # 4. Thread context (session message history)
        for msg in session.messages:
            if msg.role == "user":
                messages.append(HumanMessage(content=msg.content))
            elif msg.role == "assistant":
                messages.append(AIMessage(content=msg.content))
            # System messages from the session are intentionally skipped
            # (they would duplicate the managed system prompt above).

        # 5. Active task state
        if active_subtask is not None:
            self._inject_task_state(messages, active_subtask)

        return messages

    # ── Private helpers ──

    async def _inject_profile(self, messages: list) -> None:
        """Inject user profile memories as system messages."""
        if self._memory_store is None:
            return
        try:
            results = await self._memory_store.search(
                query="user preferences and patterns",
                tags=["user_profile"],
                top_k=3,
            )
            for entry in results:
                messages.append(
                    SystemMessage(
                        content=f"[User Profile: {entry.content}]"
                    )
                )
        except Exception:
            logger.warning('Context manager error', exc_info=True)  # Memory failure should not crash the prompt

    async def _inject_skills(self, messages: list, subtask: object) -> None:
        """Inject relevant skills as system messages."""
        if self._memory_store is None:
            return
        desc = getattr(subtask, "description", "")
        if not desc:
            return
        try:
            results = await self._memory_store.search(
                query=desc,
                tags=["skill"],
                top_k=2,
            )
            for entry in results:
                messages.append(
                    SystemMessage(
                        content=f"[Relevant Skill: {entry.content}]"
                    )
                )
        except Exception:
            pass

    def _inject_task_state(self, messages: list, subtask: object) -> None:
        """Inject current subtask status as a system message."""
        desc = getattr(subtask, "description", "")
        status_val = getattr(subtask, "status", None)
        status = status_val.value if status_val else "unknown"
        messages.append(
            SystemMessage(
                content=(
                    f"[Current Task: {desc}]\n"
                    f"Status: {status}"
                )
            )
        )
