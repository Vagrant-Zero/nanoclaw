"""Micro-compression (MC) — code-level context reduction without LLM calls.

Two strategies, triggered independently:

1. **Time-based**: If the last assistant reply is older than
   ``max_age_minutes``, all tool_result fields in the history are
   cleared (the intermediate reasoning state is no longer timely).

2. **Count-based**: If the number of compressible tool_result entries
   exceeds ``max_results``, the oldest entries are removed entirely.
"""

from __future__ import annotations

import time
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage


class MicroCompact:
    """Code-level micro-compaction for tool results.

    Operates on a message list in-place.  No LLM calls are made.
    """

    @staticmethod
    def compress_tool_result(result: str, tool_name: str) -> str:
        """Lossy compression of a tool result string by tool category.

        - ``read_file`` / ``read``: keep first 3 + last 3 lines.
        - ``command`` / ``bash`` / ``execute``: keep exit code + last 5 lines.
        - ``web`` / ``search``: keep title + first 200 chars.
        - default: truncate to 500 chars.
        """
        if not result or len(result) < 200:
            return result

        tool_lower = tool_name.lower()

        if tool_lower in ("read_file", "read", "cat", "view"):
            lines = result.splitlines()
            if len(lines) <= 6:
                return result
            head = "\n".join(lines[:3])
            tail = "\n".join(lines[-3:])
            return f"{head}\n... ({len(lines) - 6} lines omitted) ...\n{tail}"

        if tool_lower in ("command", "bash", "execute", "run", "ipython"):
            lines = result.splitlines()
            if len(lines) <= 7:
                return result
            # Try to extract exit code from the last line
            head = "\n".join(lines[:2])
            tail = "\n".join(lines[-5:])
            return f"{head}\n... ({len(lines) - 7} lines omitted) ...\n{tail}"

        if tool_lower in ("web", "search", "fetch", "http"):
            # Keep head ~200 chars
            return result[:200] + (
                f"\n... ({len(result) - 200} chars omitted) ..."
                if len(result) > 200
                else ""
            )

        # Default: truncate to 500 chars
        return result[:500] + (
            f"\n... ({len(result) - 500} chars omitted) ..."
            if len(result) > 500
            else ""
        )

    @staticmethod
    def time_based_compact(
        messages: list,
        max_age_minutes: int = 5,
    ) -> list:
        """Clear tool_result content on messages whose assistant reply
        is older than ``max_age_minutes``.

        Operates in place but also returns the list for convenience.
        """
        now = time.time()
        # Find the most recent AIMessage
        last_ai_idx = -1
        for i in range(len(messages) - 1, -1, -1):
            if isinstance(messages[i], AIMessage):
                last_ai_idx = i
                break

        if last_ai_idx < 0:
            return messages  # No assistant message — nothing to age

        # Check age via response_metadata if available
        ai_msg = messages[last_ai_idx]
        ts = getattr(ai_msg, "response_metadata", {}).get("created_at", None)
        if not ts:
            # No timestamp metadata — skip time-based check
            return messages

        age_seconds = now - (ts if isinstance(ts, (int, float)) else float(ts))
        if age_seconds < max_age_minutes * 60:
            return messages  # Still fresh

        # Clear tool_result content on ToolMessages before this AI message
        for i in range(last_ai_idx):
            if isinstance(messages[i], ToolMessage):
                messages[i].content = "[tool result omitted (expired)]"
        return messages

    @staticmethod
    def count_based_compact(messages: list, max_results: int = 8) -> list:
        """If the number of compressible tool_result entries exceeds
        ``max_results``, remove the oldest entire ToolMessages.

        Operates in place but also returns the list for convenience.
        """
        # Collect indices of compressible ToolMessages
        tool_indices = [
            i
            for i, m in enumerate(messages)
            if isinstance(m, ToolMessage) and m.content
        ]

        if len(tool_indices) <= max_results:
            return messages  # Under threshold

        # Remove oldest ToolMessages (first indices)
        excess = len(tool_indices) - max_results
        indices_to_drop = set(tool_indices[:excess])

        messages[:] = [
            m for i, m in enumerate(messages) if i not in indices_to_drop
        ]
        return messages
