"""Compression configuration — tuning constants for the three-tier
context compression strategy.

These values are safe defaults for most workloads. Override by
constructing a custom ``CompressionConfig`` instance.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CompressionConfig:
    """Configuration constants for context compression.

    Attributes:
        time_mc_max_age_minutes: Max age (minutes) since the last
            assistant reply. Older tool_results are stripped by
            time-based micro-compaction (MC).
        count_mc_max_results: Max compressible tool_results before
            count-based MC trims the oldest.
        token_threshold: Total tokens above which LLM-based
            auto-compaction is triggered.
        llm_for_summary: LLM profile/name to use for summary
            generation (can be lower-temperature than the main LLM).
        keep_last_n_turns: Number of most recent user/assistant
            turns preserved intact by auto_compact.
    """

    time_mc_max_age_minutes: int = 5
    count_mc_max_results: int = 8
    token_threshold: int = 8000
    llm_for_summary: str = "default"
    keep_last_n_turns: int = 3
