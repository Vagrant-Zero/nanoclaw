"""Tests for cron parsing and next-trigger calculation."""

from __future__ import annotations

from datetime import datetime

from nanoclaw.scheduler.cron import cron_next, is_due, parse_cron


class TestCronParsing:
    """parse_cron() — 5-field cron expression → sets."""

    def test_star(self) -> None:
        result = parse_cron("* * * * *")
        assert result["minute"] == set(range(60))
        assert result["hour"] == set(range(24))
        assert result["day_of_month"] == set(range(1, 32))

    def test_specific(self) -> None:
        result = parse_cron("0 9 * * *")
        assert result["minute"] == {0}
        assert result["hour"] == {9}

    def test_list(self) -> None:
        result = parse_cron("0,30 9,18 * * *")
        assert result["minute"] == {0, 30}
        assert result["hour"] == {9, 18}

    def test_range(self) -> None:
        result = parse_cron("0 9-17 * * *")
        assert result["hour"] == set(range(9, 18))

    def test_step(self) -> None:
        result = parse_cron("*/15 * * * *")
        assert result["minute"] == {0, 15, 30, 45}


class TestCronNext:
    """cron_next() — find next matching datetime."""

    def test_same_day(self) -> None:
        """0 9 * * *: if now is 08:00, next is today 09:00."""
        after = datetime(2026, 6, 8, 8, 0)
        nxt = cron_next("0 9 * * *", after)
        assert nxt == datetime(2026, 6, 8, 9, 0)

    def test_next_day(self) -> None:
        """0 9 * * *: if now is 10:00, next is tomorrow 09:00."""
        after = datetime(2026, 6, 8, 10, 0)
        nxt = cron_next("0 9 * * *", after)
        assert nxt == datetime(2026, 6, 9, 9, 0)

    def test_every_minute(self) -> None:
        """* * * * *: next is the following minute."""
        after = datetime(2026, 6, 8, 10, 0)
        nxt = cron_next("* * * * *", after)
        assert nxt == datetime(2026, 6, 8, 10, 1)

    def test_step_minutes(self) -> None:
        """*/15 * * * *: next is the nearest 15-min boundary."""
        after = datetime(2026, 6, 8, 10, 5)
        nxt = cron_next("*/15 * * * *", after)
        assert nxt == datetime(2026, 6, 8, 10, 15)

    def test_wrap_month(self) -> None:
        """0 0 1 * *: next is the 1st of the next month."""
        after = datetime(2026, 6, 15, 0, 0)
        nxt = cron_next("0 0 1 * *", after)
        assert nxt == datetime(2026, 7, 1, 0, 0)

    def test_wrap_year(self) -> None:
        """0 0 1 1 *: next is Jan 1 of next year."""
        after = datetime(2026, 6, 15, 0, 0)
        nxt = cron_next("0 0 1 1 *", after)
        assert nxt == datetime(2027, 1, 1, 0, 0)


class TestIsDue:
    """is_due() — check whether a task should trigger."""

    def test_never_run(self) -> None:
        assert is_due("* * * * *", None, datetime(2026, 6, 8, 10, 0))

    def test_already_run(self) -> None:
        assert not is_due("0 9 * * *", "2026-06-08T09:00:00", datetime(2026, 6, 8, 10, 0))

    def test_trigger_window(self) -> None:
        assert is_due("*/5 * * * *", "2026-06-08T10:00:00", datetime(2026, 6, 8, 10, 6))
