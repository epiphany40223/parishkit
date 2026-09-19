"""Campaign-local schedule slots shared by previews and durable work planning.

Evaluation is pure: a due slot is not permission to create, claim or deliver
work. Persistent owners still recheck current revisions, semantic coverage and
all lifecycle/recipient/hold predicates inside their ordered transaction. Local
dates are cursor/coverage identities; only resolved UTC instants decide timing.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .configuration import campaign_values, schedule_values
from .intervals import local_day, resolve_local

DAY = timedelta(days=1)


@dataclass(frozen=True)
class ScheduleSlot:
    """One stable logical slot; daily keys identify the day being reported."""

    key: str
    local_date: date
    due_at: datetime
    final_weekly: bool = False


@dataclass(frozen=True)
class SchedulePage:
    """Bounded examined-date progress, including skipped civil dates.

    An exhausted page means there is no more work through this call's cutoff, not
    that the schedule is permanently finished. Preserve the cursor to resume at a
    later cutoff. A full page may require one final empty call to prove exhaustion.
    """

    slots: tuple[ScheduleSlot, ...]
    cursor: date | None
    exhausted: bool


@dataclass(frozen=True)
class SchedulePlan:
    """Validated immutable civil configuration without subject or recipient data."""

    kind: str
    timezone: str
    start: date
    end: date
    local_time: time
    weekday: int | None
    one_time_due: datetime | None
    one_time_date: date | None

    @classmethod
    def from_values(cls, values, campaign):
        """Resolve using this campaign's versioned timezone, never parish defaults."""
        interval = campaign_values(campaign)
        due = schedule_values(values, campaign, interval=interval)
        return cls(
            values["kind"],
            campaign["timezone"],
            date.fromisoformat(campaign["start_date"]),
            date.fromisoformat(campaign["end_date"]),
            time.fromisoformat(values["time"]),
            values["weekday"],
            due,
            date.fromisoformat(values["date"]) if values["date"] else None,
        )

    def page(self, *, through, after=None, limit=100, include_final_weekly=False):
        """Examine at most ``limit`` dates, with an exclusive stable date cursor.

        Daily mail is due the following local day, including after campaign
        close. Weekly mail normally stops at close; the owning item-coverage
        planner can additionally inspect exactly one following weekly slot.
        That candidate alone never establishes a post-close obligation.
        """
        if (
            type(through) is not datetime
            or through.utcoffset() != timedelta(0)
            or (after is not None and type(after) is not date)
            or type(limit) is not int
            or not 1 <= limit <= 1000
            or type(include_final_weekly) is not bool
        ):
            raise ValueError(
                "Schedule evaluation requires a UTC cutoff and bounded cursor."
            )
        if self.kind in {"initial", "reminder"}:
            return self._one_time(through, after)
        candidate = self._first_date(after)
        last = self._last_date(include_final_weekly)
        slots, cursor = [], after
        for _ in range(limit):
            if candidate is None or candidate > last:
                return SchedulePage(tuple(slots), cursor, True)
            due = self._due(candidate)
            if due > through:
                return SchedulePage(tuple(slots), cursor, True)
            cursor = candidate
            # A wholly skipped civil day is not an active campaign day. Its
            # cursor still advances, rather than trapping a one-row batch.
            if self.kind != "daily_digest" or self._active_day(candidate):
                slots.append(
                    ScheduleSlot(
                        candidate.isoformat(),
                        candidate,
                        due,
                        self.kind == "weekly_digest" and candidate > self.end,
                    )
                )
            candidate = self._advance(candidate)
        return SchedulePage(tuple(slots), cursor, False)

    def _active_day(self, day):
        """A daily reporting obligation requires a nonempty campaign-local day."""
        interval = local_day(day, self.timezone).interval
        return interval.start < interval.end

    def next_slot(self, *, after, include_final_weekly=False):
        """Find the next configured slot without enumerating prior campaign days.

        Daily slots name yesterday, whereas weekly slots name their delivery
        day. Start near the campaign-local clock, preserving the same DST and
        skipped-civil-day rules as ordinary page evaluation. This is a schedule
        forecast, not an assertion about eligible recipients or fulfillment.
        """
        if (
            type(after) is not datetime
            or after.utcoffset() != timedelta(0)
            or type(include_final_weekly) is not bool
        ):
            raise ValueError("Next schedule slot requires a UTC cutoff.")
        if self.kind in {"initial", "reminder"}:
            if self.one_time_due <= after:
                return None
            return ScheduleSlot("once", self.one_time_date, self.one_time_due)
        local = after.astimezone(ZoneInfo(self.timezone)).date()
        offset = 2 if self.kind == "daily_digest" else 1
        cursor = local - min((local - date.min).days, offset) * DAY
        candidate = self._first_date(cursor if cursor < local else None)
        last = self._last_date(include_final_weekly)
        while candidate is not None and candidate <= last:
            due = self._due(candidate)
            if due > after and (
                self.kind != "daily_digest" or self._active_day(candidate)
            ):
                return ScheduleSlot(
                    candidate.isoformat(),
                    candidate,
                    due,
                    self.kind == "weekly_digest" and candidate > self.end,
                )
            candidate = self._advance(candidate)
        return None

    def _one_time(self, through, after):
        """Time/template revisions retain the same one-time semantic slot."""
        if after is not None and after >= self.one_time_date:
            return SchedulePage((), after, True)
        if self.one_time_due > through:
            return SchedulePage((), after, True)
        return SchedulePage(
            (ScheduleSlot("once", self.one_time_date, self.one_time_due),),
            self.one_time_date,
            True,
        )

    def _first_date(self, after):
        """Jump to the next applicable date instead of scanning prior history."""
        if after == date.max:
            return None
        first = max(self.start, after + DAY) if after is not None else self.start
        if self.kind == "weekly_digest":
            distance = (self.weekday - first.weekday()) % 7
            if (date.max - first).days < distance:
                return None
            first += timedelta(days=distance)
        return first

    def _last_date(self, include_final_weekly):
        """At most one post-close weekly candidate; no endless empty obligations."""
        if self.kind != "weekly_digest" or not include_final_weekly:
            return self.end
        distance = (self.weekday - self.end.weekday()) % 7 or 7
        if (date.max - self.end).days < distance:
            return self.end
        return self.end + timedelta(days=distance)

    def _due(self, day):
        """Use the canonical gap/fold resolver for every recurring due instant."""
        delivery_day = day + DAY if self.kind == "daily_digest" else day
        return resolve_local(
            datetime.combine(delivery_day, self.local_time), self.timezone
        )

    def _advance(self, day):
        """Return a bounded successor without overflowing the civil date range."""
        distance = 7 if self.kind == "weekly_digest" else 1
        if (date.max - day).days < distance:
            return None
        return day + timedelta(days=distance)


def preview_slots(values, campaign, *, limit=5):
    """Show a bounded beginning-of-campaign preview, not a dispatch eligibility list."""
    return SchedulePlan.from_values(values, campaign).page(
        through=datetime.max.replace(tzinfo=UTC), limit=limit
    )
