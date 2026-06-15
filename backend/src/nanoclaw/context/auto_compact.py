"""Auto-compaction — LLM-based context summarisation when token budget
is exceeded.

Uses a low-temperature LLM call to summarise older conversation turns
into a single ``SystemMessage("[Conversation Summary: ...]")`` while
preserving the most recent ``keep_last_n`` turns intact.

Original messages are saved to a ``transcript_path`` JSON file before
being replaced.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from nanoclaw.context.compression_config import CompressionConfig


def _estimate_tokens(text: str) -> int:
    """Rough token estimate for text content (chars / 3.5 ≈ tokens)."""
    return len(text) // 3 + 1


class AutoCompact:
    """LLM-based context summarisation.

    When the total context exceeds *threshold* tokens, old messages
    are summarised by the LLM and replaced with a single
    ``SystemMessage`` entry.  The most recent *keep_last_n* turns
    (each turn = one user message + one assistant message) are
    preserved verbatim.
    """

    def __init__(
        self,
        config: CompressionConfig | None = None,
        transcript_dir: str | Path | None = None,
    ) -> None:
        self._config = config or CompressionConfig()
        self._transcript_dir = (
            Path(transcript_dir) if transcript_dir else None
        )
        self.compression_count = 0

    async def compact(
        self,
        messages: list,
        llm: BaseChatModel | None = None,
        session_id: str = "",
    ) -> list:
        """Summarise old messages if total tokens exceed *threshold*.

        Args:
            messages: The message list (may be modified in place).
            llm: LangChain chat model for summary generation. If None,
                compact() is a no-op.
            session_id: Used for transcript filenames.

        Returns:
            The (possibly modified) message list.
        """
        if llm is None:
            return messages

        threshold = self._config.token_threshold
        keep_last_n = self._config.keep_last_n_turns

        total_tokens = self._count_tokens(messages)
        if total_tokens <= threshold:
            return messages

        # Identify the split point: keep the last `keep_last_n` user↔ai pairs
        split_idx = self._find_split(messages, keep_last_n)
        if split_idx <= 1:
            return messages  # Nothing to compact

        # Messages before split_idx get summarised
        old_msgs = messages[:split_idx]
        new_msgs = messages[split_idx:]

        tokens_before = self._count_tokens(old_msgs)

        # Generate a summary via LLM
        summary = await self._summarise(old_msgs, llm)
        summary_msg = SystemMessage(content=f"[Conversation Summary: {summary}]")

        tokens_after = self._estimate_tokens(summary)

        # Save transcript
        if self._transcript_dir:
            self._save_transcript(old_msgs, summary, session_id)

        # Rebuild message list
        messages.clear()
        messages.append(summary_msg)
        messages.extend(new_msgs)

        self.compression_count += 1

        # Log compression stats (caller records via EventLogger if available)
        if hasattr(llm, "_stats_hook") or True:
            # Simple inline logging
            import logging
            logger = logging.getLogger(__name__)
            logger.info(
                "Context compacted: %d → %d tokens (%.0f%% reduction, %d compressions total)",
                tokens_before,
                tokens_after,
                (1 - tokens_after / max(tokens_before, 1)) * 100,
                self.compression_count,
            )

        return messages

    def _count_tokens(self, messages: list) -> int:
        """Rough token count for the entire message list."""
        total = 0
        for m in messages:
            content = getattr(m, "content", "")
            if isinstance(content, str):
                total += _estimate_tokens(content)
        return total

    @staticmethod
    def _find_split(messages: list, keep_last_n: int) -> int:
        """Find the index to split at, preserving the last N
        user↔assistant pairs (each pair = one user + one assistant turn).

        Returns the index of the first message to keep.
        """
        pairs_found = 0
        split = len(messages)
        for i in range(len(messages) - 1, -1, -1):
            if isinstance(messages[i], (HumanMessage, type(None))):
                # Check if next message is an assistant message
                if pairs_found >= keep_last_n:
                    split = i
                    break
                # Count this as a turn start
                pairs_found += 1
            # If we've searched everything and still haven't found enough pairs
        return split if pairs_found >= keep_last_n else 0

    async def _summarise(self, messages: list, llm: BaseChatModel) -> str:
        """Ask the LLM to summarise a list of old messages."""
        text_parts = []
        for m in messages:
            role = type(m).__name__.replace("Message", "").lower()
            content = getattr(m, "content", "")
            text_parts.append(f"[{role}]: {content[:500]}")

        prompt = SystemMessage(
            content=(
                "You are a conversation summariser. Summarise the following "
                "conversation history into a concise paragraph (under 300 words). "
                "Include key facts, decisions, and user preferences.\n\n"
                + "\n".join(text_parts)
            )
        )

        try:
            response = await llm.ainvoke([prompt])
            return response.content[:1000] if hasattr(response, "content") else str(response)[:1000]
        except Exception as exc:
            import logging
            logger = logging.getLogger(__name__)
            logger.warning("Auto-compaction summary failed: %s", exc)
            return "[conversation history omitted due to summarisation error]"

    def _save_transcript(
        self,
        old_msgs: list,
        summary: str,
        session_id: str,
    ) -> None:
        """Save original messages to a transcript file before compaction."""
        if not self._transcript_dir:
            return
        self._transcript_dir.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        path = self._transcript_dir / f"compact_{session_id}_{ts}.json"

        transcript = {
            "session_id": session_id,
            "compressed_at": ts,
            "original_message_count": len(old_msgs),
            "summary": summary,
            "messages": [
                {
                    "role": type(m).__name__,
                    "content": getattr(m, "content", ""),
                }
                for m in old_msgs
            ],
        }
        path.write_text(json.dumps(transcript, ensure_ascii=False, indent=2))
