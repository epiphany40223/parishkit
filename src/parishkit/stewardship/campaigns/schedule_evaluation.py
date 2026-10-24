"""Campaign-local schedule slots shared by previews and durable work planning.

Evaluation is pure: a due slot is not permission to create, claim or deliver
work. Persistent owners still recheck current revisions, semantic coverage and
all lifecycle/recipient/hold predicates inside their ordered transaction. Local
dates are cursor/coverage identities; only resolved UTC instants decide timing.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .configuration import campaign_values, schedule_values
from .intervals import local_day, resolve_local

DAY = timedelta(days=1)


@dataclass(frozen=True)
class ScheduleSlot:
    """One stable logical slot: ``once``, or a report's slot date (#1786)."""

    key: str
    local_date: date
    due_at: datetime


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
    """One Family mailing's single send, validated in its campaign's zone.

    An Initial invitation or a Reminder sends once, on ``one_time_date`` at
    the resolved instant ``one_time_due``, under the slot key ``once``. A
    report email's values get a ``ReportPlan``, which answers the same
    questions. The Daily and Weekly digests' repeating rules retired with
    the digests (#1834): no definition of theirs remains (migration 0171
    refuses a database that still has one), so their values are refused
    here rather than evaluated.
    """

    kind: str
    one_time_due: datetime
    one_time_date: date

    @classmethod
    def from_values(cls, values, campaign):
        """Resolve using this campaign's versioned timezone, never parish defaults."""
        if values.get("kind") == "report":
            return ReportPlan.from_values(values, campaign)
        if values.get("kind") not in {"initial", "reminder"}:
            raise ValueError("Only Family mail and report emails have schedules.")
        interval = campaign_values(campaign)
        due = schedule_values(values, campaign, interval=interval)
        return cls(values["kind"], due, date.fromisoformat(values["date"]))

    def page(self, *, through, after=None, limit=100):
        """The one send once it is due by ``through`` and after the ``after`` cursor.

        Time and template revisions keep the same one-time slot, so a cursor
        at or past its date has nothing left to examine.
        """
        _check_page(through, after, limit)
        if after is not None and after >= self.one_time_date:
            return SchedulePage((), after, True)
        if self.one_time_due > through:
            return SchedulePage((), after, True)
        return SchedulePage(
            (ScheduleSlot("once", self.one_time_date, self.one_time_due),),
            self.one_time_date,
            True,
        )

    def next_slot(self, *, after):
        """The one send if it is due after the UTC instant ``after``, else None.

        This is a schedule forecast, not an assertion about eligible
        recipients or fulfillment.
        """
        if type(after) is not datetime or after.utcoffset() != timedelta(0):
            raise ValueError("Next schedule slot requires a UTC cutoff.")
        if self.one_time_due <= after:
            return None
        return ScheduleSlot("once", self.one_time_date, self.one_time_due)


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

    Cursors and slot keys are slot dates. The database
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

    def page(self, *, through, after=None, limit=100):
        """Examine at most ``limit`` candidate sends after the slot ``after``.

        The cursor is the last examined slot date, advanced past a skipped
        or paused send too; the page is exhausted at the first candidate due
        after ``through`` or when the rule ends.
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

    def next_slot(self, *, after):
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


def upcoming_slots(values, campaign, *, now, limit=5):
    """The next ``limit`` sends after ``now``, for a review, not a dispatch list.

    Each slot comes from ``SchedulePlan.next_slot``, which starts near
    ``now`` rather than scanning the campaign's earlier days, so a passed
    send is never listed (ui-conventions "Schedule previews", a count from
    now; #1129). The page is ``exhausted`` when no send follows the last
    one listed; one extra lookup tells. ``now`` is a UTC instant, the
    database's clock for a review.
    """
    plan = SchedulePlan.from_values(values, campaign)
    slots, after = [], now
    while len(slots) <= limit:
        slot = plan.next_slot(after=after)
        if slot is None:
            break
        slots.append(slot)
        after = slot.due_at
    return SchedulePage(tuple(slots[:limit]), None, len(slots) <= limit)
