"""ContextManager — assembles LLM prompts from memory, session, and task state.

The build order is::

    1. System prompt         (role definition)
    2. User profile          (from MemoryStore, ``user_profile`` tag)
    3. Skill injections      (from MemoryStore, ``skill`` tag, if active subtask)
    4. Thread context        (session message history)
    5. Active task state     (subtask status and trace, if active subtask)
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

from pathlib import Path
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from nanoclaw.context.auto_compact import AutoCompact
from nanoclaw.context.micro_compact import MicroCompact

from nanoclaw.context.compression_config import CompressionConfig
from nanoclaw.eval.events import ContextStatsEvent
from nanoclaw.eval.logger import EventLogger

if TYPE_CHECKING:
    from nanoclaw.memory.store import MemoryStore
    from nanoclaw.models.chat import ChatMessage
def _estimate_messages_tokens(messages: list) -> int:
    """Rough token count for a list of langchain messages."""
    total = 0
    for m in messages:
        content = getattr(m, "content", "")
        if isinstance(content, str):
            total += len(content) // 3 + 1
    return total


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

    def __init__(
        self,
        memory_store: MemoryStore | None = None,
        compression_config: CompressionConfig | None = None,
        auto_compact: AutoCompact | None = None,

        event_logger: EventLogger | None = None,
        transcript_dir: str | None = None,
    ) -> None:
        self._memory_store = memory_store
        self._compression_config = compression_config or CompressionConfig()
        self._event_logger = event_logger
        self._compression_count = 0
        # Wire transcript_dir into auto_compact if not already configured
        if auto_compact is not None and transcript_dir is not None:
            if auto_compact._transcript_dir is None:
                auto_compact._transcript_dir = Path(transcript_dir)
        self._auto_compact = auto_compact

    async def build_prompt(
        self,
        session: SessionContext,
        active_subtask: object | None = None,
        llm: BaseChatModel | None = None,
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

        # ── Compression pipeline ────────────────────────────────────
        if self._compression_config is not None:
            # Step A0: Code-level MC — compress individual tool results
            for msg in messages:
                if isinstance(msg, ToolMessage):
                    tool_name = getattr(msg, "name", "unknown")
                    content_str = str(getattr(msg, "content", ""))
                    if content_str:
                        msg.content = MicroCompact.compress_tool_result(
                            content_str, tool_name,
                        )
                elif isinstance(msg, AIMessage):
                    # Compress large AI messages (e.g. task plans)
                    content_str = str(getattr(msg, "content", ""))
                    if len(content_str) > 1500:
                        msg.content = (
                            content_str[:1500]
                            + f"\n... ({len(content_str) - 1500} chars omitted) ..."
                        )

            # Step A: Time-based MC — clear expired tool_results
            MicroCompact.time_based_compact(
                messages,
                self._compression_config.time_mc_max_age_minutes,
            )
            # Step B: Count-based MC — trim excess tool_results
            MicroCompact.count_based_compact(
                messages,
                self._compression_config.count_mc_max_results,
            )
            # Step C: Token-based auto-compact (LLM summary)
            if self._auto_compact is not None and llm is not None:
                tokens_before = _estimate_messages_tokens(messages)
                messages = await self._auto_compact.compact(
                    messages, llm, session.id,
                )
                tokens_after = _estimate_messages_tokens(messages)
                self._compression_count = self._auto_compact.compression_count

                # Log ContextStatsEvent (Subtask 5.5)
                stats = ContextStatsEvent(
                    session_id=session.id,
                    total_tokens=tokens_before,
                    compression_count=self._compression_count,
                    tokens_before=tokens_before,
                    tokens_after=tokens_after,
                )
                if self._event_logger is not None:
                    await self._event_logger.log_event(
                        session.id, "context_stats", stats,
                    )
                else:
                    import logging as _logging
                    _logging.getLogger(__name__).info(
                        "ContextStats — session=%s total=%d compressions=%d before=%d after=%d",
                        session.id, tokens_before,
                        self._compression_count,
                        tokens_before, tokens_after,
                    )

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
