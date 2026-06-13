"""Tests for MemoryScheduledTaskRepo — due task detection."""

from __future__ import annotations

from datetime import datetime

import pytest

from nanoclaw.scheduler import MemoryScheduledTaskRepo, ScheduledTask
from nanoclaw.scheduler.cron import cron_next


class TestScheduledTaskRepo:
    """ScheduledTaskRepo.get_due_tasks logic."""

    @pytest.mark.asyncio
    async def test_never_run_is_due(self) -> None:
        """Never-run enabled tasks should be immediately due."""
        repo = MemoryScheduledTaskRepo()
        await repo.create(ScheduledTask(
            description="t", prompt="p", schedule="0 9 * * *",
        ))
        assert len(await repo.get_due_tasks()) == 1

    @pytest.mark.asyncio
    async def test_disabled_never_run_not_due(self) -> None:
        """Disabled tasks should never be due."""
        repo = MemoryScheduledTaskRepo()
        await repo.create(ScheduledTask(
            description="t", prompt="p", schedule="* * * * *",
            enabled=False,
        ))
        assert len(await repo.get_due_tasks()) == 0

    @pytest.mark.asyncio
    async def test_just_run_not_due(self) -> None:
        """A task that just ran should not be due again immediately."""
        from datetime import datetime, timedelta

        repo = MemoryScheduledTaskRepo()
        t = await repo.create(ScheduledTask(
            description="t", prompt="p", schedule="0 9 * * *",
        ))
        # Use a timestamp 1 minute ago — next cron trigger is in the future
        recent = (datetime.now() - timedelta(minutes=1)).isoformat()
        await repo.update_last_run(t.id, recent)
        due = await repo.get_due_tasks()
        assert len(due) == 0

    @pytest.mark.asyncio
    async def test_every_minute_is_due_after_interval(self) -> None:
        """*/5 * * * * should be due once 5 minutes pass."""
        repo = MemoryScheduledTaskRepo()
        t = await repo.create(ScheduledTask(
            description="t", prompt="p", schedule="*/5 * * * *",
        ))
        # Simulate last run 6 minutes ago
        await repo.update_last_run(t.id, "2026-06-08T09:00:00")
        due = await repo.get_due_tasks()
        # The test runs in real time, so "now" is after 09:05
        assert len(due) == 1

    @pytest.mark.asyncio
    async def test_multiple_tasks(self) -> None:
        """Multiple tasks should all be checked."""
        repo = MemoryScheduledTaskRepo()
        await repo.create(ScheduledTask(description="a", prompt="p", schedule="* * * * *"))
        await repo.create(ScheduledTask(description="b", prompt="p", schedule="0 9 * * *"))
        await repo.create(ScheduledTask(description="c", prompt="p", schedule="0 9 * * *", enabled=False))
        # a is due (every minute), b might or might not be (9 AM)
        due = await repo.get_due_tasks()
        assert len(due) >= 1
        assert all(t.enabled for t in due)
