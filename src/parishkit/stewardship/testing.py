"""Explicit test doubles; never selectable from production deployment settings."""

from datetime import datetime, timedelta

from .clock import utc_instant


class ManualClock:
    """Advance scenario time instantly without sleeps or global clock patching.

    Store UTC so advancing across DST represents elapsed time, not ambiguous
    local-wall arithmetic. One instance is owned by one test/scenario; future
    multi-process integration tests need a transaction-backed clock adapter.
    """

    def __init__(self, instant: datetime):
        """Start at an explicit, validated instant."""
        self._instant = utc_instant(instant)

    def now(self) -> datetime:
        """Read the unchanged scenario instant until explicitly advanced."""
        return self._instant

    def advance(self, elapsed: timedelta) -> datetime:
        """Move forward by elapsed time; reject accidental time reversal."""
        if not isinstance(elapsed, timedelta) or elapsed < timedelta(0):
            raise ValueError("elapsed time must be a nonnegative timedelta")
        self._instant += elapsed
        return self._instant
