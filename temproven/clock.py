"""
temproven/clock.py
──────────────────
Injectable clock abstraction.

WHY: Domain logic (coverage checks, incident triggers) must never call
     datetime.now() directly. If it did, the demo and tests would give
     different results each run, and we couldn't reproduce the Act 1 /
     Act 2 "before / after" behavior.

USAGE:
    # Real time (used by the live application)
    clock = RealClock()

    # Fixed time (used by the CLI demo and tests)
    clock = FixedClock(datetime(2026, 10, 1, 0, 0, 0, tzinfo=timezone.utc))
    # Or integer epoch timestamp:
    clock = FixedClock(1782864000)

    # Advance the demo clock to a new moment
    clock.advance_to(datetime(2026, 10, 17, 0, 0, 0, tzinfo=timezone.utc))

    # Get current time as a UTC datetime
    now: datetime = clock.now()

    # Get current time as integer epoch seconds (used in Cypher comparisons)
    ts: int = clock.now_epoch()
"""

from __future__ import annotations

from datetime import datetime, timezone


class Clock:
    """Abstract base — never instantiate directly."""

    def now(self) -> datetime:
        raise NotImplementedError

    def now_epoch(self) -> int:
        """Return current time as integer Unix epoch seconds (UTC)."""
        return int(self.now().timestamp())


class RealClock(Clock):
    """Returns real wall-clock UTC time. Use in the live application."""

    def now(self) -> datetime:
        return datetime.now(tz=timezone.utc)


class FixedClock(Clock):
    """
    Returns a fixed, manually-advanced UTC time.
    Use in the CLI demo and tests so results are always reproducible.
    Accepts either a timezone-aware datetime or integer epoch seconds.
    """

    def __init__(self, fixed_time: datetime | int | float) -> None:
        if isinstance(fixed_time, (int, float)):
            self._time = datetime.fromtimestamp(int(fixed_time), tz=timezone.utc)
        elif isinstance(fixed_time, datetime):
            if fixed_time.tzinfo is None:
                raise ValueError(
                    "FixedClock requires a timezone-aware datetime. "
                    "Pass tzinfo=timezone.utc explicitly."
                )
            self._time = fixed_time
        else:
            raise TypeError(f"Expected datetime or int, got {type(fixed_time)}")

    def now(self) -> datetime:
        return self._time

    def advance_to(self, new_time: datetime | int | float) -> None:
        """Move the clock forward to a new moment (used between demo acts)."""
        if isinstance(new_time, (int, float)):
            dt = datetime.fromtimestamp(int(new_time), tz=timezone.utc)
        elif isinstance(new_time, datetime):
            if new_time.tzinfo is None:
                raise ValueError("advance_to requires a timezone-aware datetime.")
            dt = new_time
        else:
            raise TypeError(f"Expected datetime or int, got {type(new_time)}")

        if dt < self._time:
            raise ValueError(
                f"advance_to must move forward in time. "
                f"Current: {self._time.isoformat()}, Requested: {dt.isoformat()}"
            )
        self._time = dt

    def advance(self, seconds: int | float) -> None:
        """Advance the clock forward by the given number of seconds."""
        if seconds < 0:
            raise ValueError(f"advance must be non-negative, got {seconds}")
        self.advance_to(self.now_epoch() + int(seconds))

