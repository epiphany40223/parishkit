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
    """Validated immutable civil configuration without subject or recipient data.

    ``skipped`` holds a digest's ``skip_dates`` (#1658): slot dates it never
    sends. Every evaluation leaves them out, advancing its cursor past them
    as past a wholly skipped civil day, so planners, catch-up and forecasts
    agree with the database's slot check (``stewardship_catchup_due_v1``).
    """

    kind: str
    timezone: str
    start: date
    end: date
    local_time: time
    weekday: int | None
    one_time_due: datetime | None
    one_time_date: date | None
    skipped: frozenset[date] = frozenset()

    @classmethod
    def from_values(cls, values, campaign):
        """Resolve using this campaign's versioned timezone, never parish defaults.

        A report email's values (``kind`` ``report``) get a ``ReportPlan``,
        which answers the same ``page`` and ``next_slot`` questions.
        """
        if values.get("kind") == "report":
            return ReportPlan.from_values(values, campaign)
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
            frozenset(map(date.fromisoformat, values.get("skip_dates", ()))),
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
            # cursor still advances, rather than trapping a one-row batch, and
            # so does a skipped send's (#1658).
            if self._sends(candidate):
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

    def _sends(self, day):
        """Whether a candidate date in range is a send: active and not skipped."""
        if day in self.skipped:
            return False
        return self.kind != "daily_digest" or self._active_day(day)

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
            if due > after and self._sends(candidate):
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


def _check_page(through, after, limit):
    """Refuse a cutoff that is not UTC or a cursor or limit out of bounds."""
    if (
        type(through) is not datetime
        or through.utcoffset() != timedelta(0)
        or (after is not None and type(after) is not date)
        or type(limit) is not int
        or not 1 <= limit <= 1000
    ):
        raise ValueError(
            "Schedule evaluation requires a UTC cutoff and bounded cursor."
        )


@dataclass(frozen=True)
class ReportPlan:
    """One report email's sends (report-emails spec, "Schedules", #1786).

    The candidate sends are the one-time date, or the repeat rule's days
    between its bounds (``report_values.repeat_bounds``; ``last`` None
    means until the campaign is archived). A candidate is a send unless its
    slot is in ``skipped``, its due instant falls in a pause interval
    (``pauses``, each ``(start, end)`` with ``end`` None while paused), or,
    for an every-day rule, its slot's civil day has no moments. ``offset``
    is the slot basis: a ``report_date`` slot names the day before its send
    (the converted Daily), a ``send_date`` slot the send's own day.

    Cursors and slot keys are slot dates, as the digests' are. The database
    evaluates the same rule (``stewardship_report_slot_due_v1``); the shared
    table ``tests/stewardship/fixtures/report_slot_cases.json`` pins the two
    together.
    """

    timezone: str
    local_time: time
    once: date | None
    frequency: str | None
    weekdays: frozenset[int]
    first: date | None
    last: date | None
    offset: int
    skipped: frozenset[date] = frozenset()
    pauses: tuple = ()

    @classmethod
    def from_values(cls, values, campaign):
        """Resolve a validated report's rule against its campaign's dates."""
        from .report_values import instant, repeat_bounds, schedule_fields

        schedule_fields(values)
        start = date.fromisoformat(campaign["start_date"])
        end = date.fromisoformat(campaign["end_date"])
        schedule = values["schedule"]
        once = schedule["type"] == "once"
        first, last = (None, None) if once else repeat_bounds(schedule, start, end)
        return cls(
            campaign["timezone"],
            time.fromisoformat(schedule["time"]),
            date.fromisoformat(schedule["date"]) if once else None,
            None if once else schedule["frequency"],
            frozenset(schedule.get("weekdays", ())),
            first,
            last,
            1 if values["slot_basis"] == "report_date" else 0,
            frozenset(map(date.fromisoformat, values["skip_dates"])),
            tuple(
                (instant(entry["start"]), entry["end"] and instant(entry["end"]))
                for entry in values["paused_ranges"]
            ),
        )

    @property
    def one_time_due(self):
        """The one-time send's instant, or None for a repeating report.

        ``SchedulePlan``'s field of the same name, which the schedule table
        reads for every row, a converted Daily or Weekly report's included.
        """
        return None if self.once is None else self._due(self.once)

    def _candidate(self, send):
        """Whether ``send`` is a send date of the rule, before any exclusion."""
        if self.once is not None:
            return send == self.once
        if send < self.first or (self.last is not None and send > self.last):
            return False
        return self.frequency == "daily" or send.weekday() in self.weekdays

    def _next(self, send):
        """The first candidate send date after ``send``, or None.

        Every candidate is at most seven days after the last, so the search
        is bounded; a one-time report has only its own date.
        """
        if self.once is not None:
            return self.once if send < self.once else None
        if send < self.first:
            send = self.first - DAY
        for distance in range(1, 8):
            if (date.max - send).days < distance:
                return None
            day = send + timedelta(days=distance)
            if self.last is not None and day > self.last:
                return None
            if self._candidate(day):
                return day
        return None

    def _due(self, send):
        """The send's instant: its campaign-local time on its send date."""
        return resolve_local(datetime.combine(send, self.local_time), self.timezone)

    def _sends(self, send, due):
        """Whether a candidate is a send: not skipped, paused or a missing day."""
        slot = send - timedelta(days=self.offset)
        if slot in self.skipped or any(
            start <= due and (end is None or due < end) for start, end in self.pauses
        ):
            return False
        if self.frequency == "daily":
            interval = local_day(slot, self.timezone).interval
            return interval.start < interval.end
        return True

    def _slot(self, send, due):
        """The candidate's slot: keyed by its slot date (spec, "Slot keys")."""
        slot = send - timedelta(days=self.offset)
        return ScheduleSlot(slot.isoformat(), slot, due)

    def _start(self, after):
        """The send date to search from: just before the cursor's next slot."""
        if after is None:
            return date.min
        return after + timedelta(days=self.offset)

    def page(self, *, through, after=None, limit=100, include_final_weekly=False):
        """Examine at most ``limit`` candidate sends after the slot ``after``.

        As ``SchedulePlan.page``: the cursor is the last examined slot date,
        advanced past a skipped or paused send too; the page is exhausted at
        the first candidate due after ``through`` or when the rule ends.
        ``include_final_weekly`` is accepted for the shared interface and
        changes nothing: a report's post-close sends are ordinary sends.
        """
        _check_page(through, after, limit)
        send, slots, cursor = self._next(self._start(after)), [], after
        for _ in range(limit):
            if send is None:
                return SchedulePage(tuple(slots), cursor, True)
            due = self._due(send)
            if due > through:
                return SchedulePage(tuple(slots), cursor, True)
            cursor = send - timedelta(days=self.offset)
            if self._sends(send, due):
                slots.append(self._slot(send, due))
            send = self._next(send)
        return SchedulePage(tuple(slots), cursor, send is None)

    def next_slot(self, *, after, include_final_weekly=False):
        """The first send due after the UTC instant ``after``, or None.

        The search starts two days before ``after``'s campaign-local date,
        so a daylight-saving shift cannot pass over a send. An open pause
        interval that has begun ends the search: no later send is made
        while it stays open.
        """
        if type(after) is not datetime or after.utcoffset() != timedelta(0):
            raise ValueError("Next schedule slot requires a UTC cutoff.")
        local = after.astimezone(ZoneInfo(self.timezone)).date()
        send = self._next(local - min((local - date.min).days, 2) * DAY)
        while send is not None:
            due = self._due(send)
            if any(end is None and start <= due for start, end in self.pauses):
                return None
            if due > after and self._sends(send, due):
                return self._slot(send, due)
            send = self._next(send)
        return None


def preview_slots(values, campaign, *, limit=5):
    """Show a bounded beginning-of-campaign preview, not a dispatch eligibility list."""
    return SchedulePlan.from_values(values, campaign).page(
        through=datetime.max.replace(tzinfo=UTC), limit=limit
    )
