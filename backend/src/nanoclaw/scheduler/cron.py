"""Cron expression parsing and next-trigger calculation.

Supports 5-field cron format: ``minute hour day-of-month month day-of-week``
with ``*``, numbers, lists (``1,3,5``), ranges (``1-5``), and step values (``*/5``).
"""

from __future__ import annotations

import calendar
from datetime import datetime, timedelta


def parse_cron(expr: str) -> dict[str, set[int]]:
    """Parse a 5-field cron expression into sets of valid values."""
    fields = expr.strip().split()
    if len(fields) != 5:
        raise ValueError(f"Expected 5 cron fields, got {len(fields)}")

    field_names = ["minute", "hour", "day_of_month", "month", "day_of_week"]
    ranges = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 6)]

    result: dict[str, set[int]] = {}
    for field, name, (lo, hi) in zip(fields, field_names, ranges):
        result[name] = _parse_field(field, lo, hi)
    return result


def _parse_field(field: str, lo: int, hi: int) -> set[int]:
    """Parse a single cron field into a set of valid integers."""
    values: set[int] = set()
    for part in field.split(","):
        part = part.strip()
        if not part:
            continue
        if "/" in part:
            base, step_s = part.split("/", 1)
            step = int(step_s)
            if base == "*":
                base_range = range(lo, hi + 1)
            elif "-" in base:
                bl, bh = map(int, base.split("-", 1))
                base_range = range(bl, bh + 1)
            else:
                base_range = range(int(base), hi + 1)
            values.update(base_range[::step])
        elif "-" in part:
            a, b = map(int, part.split("-", 1))
            values.update(range(min(a, b), max(a, b) + 1))
        elif part == "*":
            values.update(range(lo, hi + 1))
        else:
            values.add(int(part))
    return values


def cron_next(expr: str, after: datetime) -> datetime:
    """Find the *next* datetime matching *expr* on or after *after*.

    Iterates minute by minute (up to 4 years forward) through matching
    candidates. Raises ``ValueError`` if no match is found.
    """
    parsed = parse_cron(expr)
    # Start at the next full minute
    dt = after.replace(second=0, microsecond=0) + timedelta(minutes=1)

    # Max iterations = 4 years of minutes
    for _ in range(4 * 366 * 24 * 60):
        # ---- month ----
        if dt.month not in parsed["month"]:
            y = dt.year + 1 if dt.month == 12 else dt.year
            m = 1 if dt.month == 12 else dt.month + 1
            dt = dt.replace(year=y, month=m, day=1, hour=0, minute=0)
            continue

        # ---- day (day_of_month OR day_of_week) ----
        _dom_all = parsed["day_of_month"] == set(range(1, 32))
        _dow_all = parsed["day_of_week"] == set(range(0, 7))
        if _dom_all and _dow_all:
            _day_ok = True
        elif _dom_all:
            _day_ok = dt.weekday() in parsed["day_of_week"]
        elif _dow_all:
            _day_ok = dt.day in parsed["day_of_month"]
        else:
            _day_ok = dt.day in parsed["day_of_month"] or dt.weekday() in parsed["day_of_week"]
        if not _day_ok:
            dt += timedelta(days=1)
            dt = dt.replace(hour=0, minute=0)
            continue

        # ---- hour ----
        if dt.hour not in parsed["hour"]:
            h = dt.hour + 1
            if h > 23:
                dt += timedelta(days=1)
                dt = dt.replace(hour=0, minute=0)
            else:
                dt = dt.replace(hour=h, minute=0)
            continue

        # ---- minute ----
        if dt.minute in parsed["minute"]:
            return dt

        dt += timedelta(minutes=1)

    raise ValueError(
        f"Could not find next cron time for {expr!r} after {after}"
    )


def is_due(
    task_schedule: str,
    last_run_iso: str | None,
    now: datetime | None = None,
) -> bool:
    """Check whether a scheduled task is due for execution.

    A task is due when:
    * It has never run (``last_run is None``) **or**
    * The ``cron_next`` time after ``last_run`` has already passed.
    """
    if now is None:
        now = datetime.now()
    if last_run_iso is None:
        return True
    last = datetime.fromisoformat(last_run_iso)
    nxt = cron_next(task_schedule, last)
    return nxt <= now
