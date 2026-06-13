"""DreamingCronTrigger — decides when to run the daily dreaming process.

Default schedule: 02:00 daily.  The trigger is stateful: it remembers the
last date it triggered and will not fire again until the next calendar day.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nanoclaw.dreaming.engine import DreamingEngine

_DREAMING_HOUR = 2


class DreamingCronTrigger:
    """Daily dreaming trigger, default 02:00.

    Usage::

        trigger = DreamingCronTrigger(dreaming_engine)
        await trigger.check()  # Returns True if dreaming was triggered
    """

    def __init__(self, dreaming_engine: DreamingEngine) -> None:
        self._engine = dreaming_engine
        self._last_date: str = ""

    async def check(self) -> bool:
        """Check whether dreaming should run now.

        Returns True if dreaming was triggered (at most once per day).
        """
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        if self._last_date == today:
            return False
        if now.hour < _DREAMING_HOUR:
            return False
        self._last_date = today
        yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
        await self._engine.run_dreaming(yesterday)
        return True
