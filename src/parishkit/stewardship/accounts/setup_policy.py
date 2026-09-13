"""Bounded initial-setup lifetime policy shared by web, worker and cleanup owners.

These values are observations supplied by authenticated, locked database owners,
not credentials or permission to resume a wizard. In particular, no request may
substitute another session or TaskRun for the attempt's immutable bindings.
The setup-attempt and setup-draft SQL guards also enforce the frozen intervals;
their installed-policy tests must accompany any future interval migration.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID

from .session_policy import ADMIN_IDLE as IDLE_LIMIT

RENEWAL_INTERVAL = timedelta(minutes=5)
SOURCE_WATCHDOG = timedelta(hours=2)


class SetupState(StrEnum):
    """Expired attempts retain only safe tombstones and never become resumable."""

    COLLECTING = "collecting"
    LOADING = "loading"
    FROZEN = "frozen"
    COMPLETED = "completed"
    EXPIRED = "expired"


class ExpiryReason(StrEnum):
    """Closed cleanup reasons contain no credentials or staged configuration values."""

    CANCELLED = "cancelled"
    SESSION = "session"
    IDLE = "idle"
    WATCHDOG = "watchdog"
    ABSOLUTE = "absolute"


def _instant(value):
    """Database observations are timezone-aware; never interpret a naive wall clock."""
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("Setup lifetime requires an aware instant.")
    return value


@dataclass(frozen=True)
class SetupWindow:
    """Immutable session/load binding plus exact observed deadlines and renewal time."""

    session_id: UUID
    state: SetupState
    activity_at: datetime
    absolute_at: datetime
    task_id: UUID | None = None
    task_created_at: datetime | None = None
    renewed_at: datetime | None = None

    def __post_init__(self):
        """Reject partial load identities and impossible persisted time ordering."""
        if (
            not isinstance(self.session_id, UUID)
            or not isinstance(self.state, SetupState)
            or _instant(self.activity_at) >= _instant(self.absolute_at)
            or (self.task_id is None) != (self.task_created_at is None)
            or (self.task_id is not None and not isinstance(self.task_id, UUID))
            or (self.state is SetupState.LOADING and self.task_id is None)
        ):
            raise ValueError("Invalid setup lifetime binding.")
        if (
            self.task_created_at is not None
            and _instant(self.task_created_at) >= self.absolute_at
        ):
            raise ValueError("Source work cannot start after the session lifetime.")
        if self.renewed_at is not None and (
            self.task_created_at is None
            or not self.task_created_at <= _instant(self.renewed_at) <= self.activity_at
        ):
            raise ValueError("Invalid setup renewal observation.")

    @property
    def idle_at(self):
        """The UI displays the real idle deadline, capped by absolute expiry."""
        return min(self.activity_at + IDLE_LIMIT, self.absolute_at)

    @property
    def watchdog_at(self):
        """The source Task's creation time is never reset by a retry or heartbeat."""
        return self.task_created_at + SOURCE_WATCHDOG if self.task_created_at else None

    def expiry(self, now, *, session_live):
        """Return the cleanup reason without granting access or changing any state."""
        _instant(now)
        if type(session_live) is not bool:
            raise TypeError("Session liveness must come from authenticated admission.")
        if self.state is SetupState.COMPLETED:
            return None
        if self.state is SetupState.EXPIRED or not session_live:
            return ExpiryReason.SESSION
        if now >= self.absolute_at:
            return ExpiryReason.ABSOLUTE
        if self.state is SetupState.LOADING and now >= self.watchdog_at:
            return ExpiryReason.WATCHDOG
        if now >= self.idle_at:
            return ExpiryReason.IDLE
        return None

    def may_renew(self, now, *, session_id, task_id, worker_live, session_live):
        """Allow only the same live source progress request, once per five minutes.

        The caller separately enforces current Admin, CSRF, exact page correlation
        and the worker's real task/source fences. A terminal load changes the
        attempt out of LOADING before this predicate is evaluated. Read-only
        GET polling and other setup tasks must never call this renewal path.
        """
        if type(worker_live) is not bool:
            raise TypeError("Worker liveness must come from current lease evidence.")
        if self.expiry(now, session_live=session_live) is not None:
            return False
        if (
            self.state is not SetupState.LOADING
            or session_id != self.session_id
            or task_id != self.task_id
            or not worker_live
            or now < self.task_created_at
        ):
            return False
        previous = self.renewed_at or self.task_created_at
        return now >= previous + RENEWAL_INTERVAL
