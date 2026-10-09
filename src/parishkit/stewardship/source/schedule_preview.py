"""The refresh schedule's seven-day preview, cost and freshness (#632).

The ParishSoft settings page (and later the command line) shows, for an
edited and not yet saved schedule, what the scheduler will do over the next
seven days (see the admin-portal spec, "Seven-day preview" and "Cost and
freshness summary"):

- every daily time of each day at the instant it runs, resolved through the
  scheduler's own daylight-saving resolver (``cadence.local_instant``) and
  tie rule (``cadence.precedence``);
- every time that will not run, with its reason: covered by a full refresh
  (``refresh_rules.daily_times``: coverage, or precedence at the same
  time), skipped inside a Family email's window (``send_windows.window_cause``,
  only when the schedule skips around Family emails), or joined to another
  time at the same instant on the day clocks go forward;
- the estimated daily ParishSoft time, from the medians of recent successful
  runs, or typical values until there are enough;
- the longest wait for a new full refresh, and the alarm's margin.

The pure functions take every input as an argument and are unit tested
without a database; a parity test runs the scheduler's own slot functions
across both daylight-saving days and checks they create exactly the slots
the preview says run. The readers at the end feed them from durable records.
Nothing here writes anything.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from statistics import median
from zoneinfo import ZoneInfo

from parishkit.stewardship.campaigns.intervals import resolve_local

from .cadence import local_instant, precedence
from .refresh_rules import daily_times
from .send_windows import window_cause

# How many days the preview shows, today first.
DAYS = 7
# Typical durations until there are ``MINIMUM_RUNS`` measured runs of a
# kind: Production's medians in October 2026 (7.3 and 2.2 minutes).
TYPICAL = {"full": timedelta(minutes=7.3), "quick": timedelta(minutes=2.2)}
MINIMUM_RUNS = 3
# How far back measured runs count.
MEASURED_DAYS = timedelta(days=7)
# Above this share of the day the cost summary warns; it never refuses.
WARNING_SHARE = 0.25


@dataclass(frozen=True)
class PreviewTime:
    """One daily time on one day of the preview.

    ``status`` is "runs", "covered" (a quick time a full refresh covers,
    ``by``), "skipped" (inside the Family email window ``window``) or
    "joined" (on the day clocks go forward, it resolves to the same instant
    as the time ``by`` and runs as part of it, or not at all for a full time
    the tie rule passes over). ``due_at`` is the UTC instant it falls due at,
    None when covered. ``shift`` is "forward" when it runs later than its
    wall time because clocks go forward, "back" when its wall time repeats
    because clocks go back (it runs once, at the earlier instant). A nightly
    refresh is never skipped; ``window`` then names the window it runs in.
    """

    kind: str
    time: str
    status: str
    due_at: datetime | None = None
    by: str | None = None
    window: str | None = None
    nightly: bool = False
    shift: str | None = None


@dataclass(frozen=True)
class PreviewDay:
    """One local day: its bounds (UTC), its clock change, and its times.

    ``change`` is "forward" or "back" on a daylight-saving day, else None.
    ``times`` are in wall-time order, a full time before a quick one.
    """

    day: object
    start: datetime
    end: datetime
    change: str | None
    times: tuple

    def runs(self, kind):
        """The times of ``kind`` that run as their own refresh that day."""
        return [t for t in self.times if t.kind == kind and t.status == "runs"]


@dataclass(frozen=True)
class Durations:
    """How long a full refresh and a quick update take, and whether measured."""

    full: timedelta
    quick: timedelta
    full_measured: bool = False
    quick_measured: bool = False


@dataclass(frozen=True)
class Cost:
    """The estimated ParishSoft time: per preview day and on average.

    ``per_day`` pairs each day's time with its share of that day (a
    daylight-saving day is 23 or 25 hours long); ``warning`` says the
    average share is above ``WARNING_SHARE``.
    """

    per_day: tuple
    average: timedelta
    share: float
    warning: bool
    durations: Durations


@dataclass(frozen=True)
class Freshness:
    """The longest wait for a new full refresh, and when the alarm sounds.

    ``start`` and ``end`` are the two full refreshes (UTC) around the
    longest wait; None when fewer than two full refreshes run. ``margin`` is
    how late a full refresh may be before Administrators are alerted.
    """

    longest: timedelta | None
    start: datetime | None
    end: datetime | None
    margin: timedelta


def _day_bounds(day, timezone):
    """The UTC instants local ``day`` starts and ends."""
    start = resolve_local(datetime.combine(day, time()), timezone)
    end = resolve_local(datetime.combine(day + timedelta(days=1), time()), timezone)
    return start, end


def _change(start, end, timezone):
    """Whether clocks go "forward" or "back" during the day, else None."""
    zone = ZoneInfo(timezone)
    before, after = start.astimezone(zone).utcoffset(), end.astimezone(zone).utcoffset()
    if after > before:
        return "forward"
    return "back" if after < before else None


def _shift(day, value, due, timezone):
    """How a clock change moves the wall time ``value`` on ``day``, or None.

    "forward" when it falls in the missing hour (it runs at the first real
    instant after), "back" when it falls in the repeated hour (it runs once,
    at the earlier instant).
    """
    zone = ZoneInfo(timezone)
    wall = datetime.combine(day, time.fromisoformat(value))
    if due.astimezone(zone).replace(tzinfo=None) != wall:
        return "forward"
    folds = {wall.replace(tzinfo=zone, fold=fold).utcoffset() for fold in (0, 1)}
    return "back" if len(folds) > 1 else None


def _join(times, nightly):
    """Mark the times that resolve to one instant as joined, as the scheduler runs them.

    Only on the day clocks go forward can two daily times share an instant.
    The scheduler creates one slot per kind for it: among full times the
    latest by ``cadence.precedence`` (the nightly, then the later wall
    time); the others never run. A quick time due with a running full one
    joins it (its request coalesces into the full one); quick times due
    together share one slot identity, so they run once.
    """
    groups = {}
    for entry in times:
        if entry.status == "runs":
            groups.setdefault(entry.due_at, []).append(entry)
    joined = {}
    for due, group in groups.items():
        full = [entry for entry in group if entry.kind == "full"]
        if full:
            winner = max(full, key=lambda e: precedence(due, e.time, nightly))
        else:
            winner = max(group, key=lambda e: e.time)
        for entry in group:
            if entry is not winner:
                joined[id(entry)] = winner.time
    return [
        PreviewTime(
            entry.kind,
            entry.time,
            "joined",
            entry.due_at,
            by=joined[id(entry)],
            nightly=entry.nightly,
            shift=entry.shift,
        )
        if id(entry) in joined
        else entry
        for entry in times
    ]


def preview_days(refresh_rules, *, timezone, first, days=DAYS, windows=()):
    """What the schedule does on ``days`` local days from ``first``.

    ``refresh_rules`` must have the closed shape (``ValueError``
    otherwise); its problems, if any, are ``check_schedule``'s to report,
    and the preview still shows what the rules give. ``windows`` are the
    Family email windows (``send_windows.Window``); they skip a time only
    when the rules skip around Family emails, and never the nightly
    refresh, the earliest full time. Returns a tuple of ``PreviewDay``.
    """
    result = daily_times(refresh_rules)
    skipping = refresh_rules["skip_around_family_emails"]
    nightly = result.full[0] if result.full else None
    found = []
    for offset in range(days):
        day = first + timedelta(days=offset)
        start, end = _day_bounds(day, timezone)
        times = []
        for kind, values in (("full", result.full), ("quick", result.quick)):
            for value in values:
                due = local_instant(day, value, timezone)
                is_nightly = kind == "full" and value == nightly
                cause = window_cause(windows, due) if skipping else None
                times.append(
                    PreviewTime(
                        kind,
                        value,
                        "skipped" if cause and not is_nightly else "runs",
                        due,
                        window=cause,
                        nightly=is_nightly,
                        shift=_shift(day, value, due, timezone),
                    )
                )
        times = _join(times, nightly)
        times.extend(
            PreviewTime("quick", value, "covered", by=by)
            for value, by in (
                *result.covered,
                *((value, value) for value in result.replaced),
            )
        )
        times.sort(key=lambda t: (t.time, t.kind != "full"))
        found.append(
            PreviewDay(day, start, end, _change(start, end, timezone), tuple(times))
        )
    return tuple(found)


def durations(samples):
    """``Durations`` from measured run lengths: ``{"full": [...], "quick": [...]}``.

    Each kind's median once it has ``MINIMUM_RUNS`` runs, else its typical
    value (``TYPICAL``), which the page labels as such.
    """
    found = {}
    for kind in ("full", "quick"):
        values = samples.get(kind, ())
        measured = len(values) >= MINIMUM_RUNS
        found[kind] = (median(values) if measured else TYPICAL[kind], measured)
    return Durations(
        found["full"][0], found["quick"][0], found["full"][1], found["quick"][1]
    )


def cost(days, measured):
    """The estimated ParishSoft time of each day and on average (``Cost``).

    Each day's runs (joined, covered and skipped times cost nothing) times
    the ``measured`` durations, and its share of that day's length.
    """
    per_day = []
    for day in days:
        spent = (
            len(day.runs("full")) * measured.full
            + len(day.runs("quick")) * measured.quick
        )
        per_day.append((spent, spent / (day.end - day.start)))
    average = sum((spent for spent, _ in per_day), timedelta()) / len(per_day)
    share = sum(part for _, part in per_day) / len(per_day)
    return Cost(tuple(per_day), average, share, share > WARNING_SHARE, measured)


def freshness(days, margin, *, since=None):
    """The longest wait between full refreshes that run over ``days``.

    Counts the excluded windows: a skipped full refresh brings no new data.
    Quick updates do not count; they check the connection and catch the
    Family contact changes ParishSoft reports, but do not re-read the data.
    Only waits that end after ``since`` count, so the caller can pass the
    day before the preview as the first of ``days`` and still measure the
    wait into the preview's first morning. The earliest of equal waits wins.
    """
    instants = sorted({t.due_at for day in days for t in day.runs("full")})
    gaps = [
        (b - a, a, b)
        for a, b in zip(instants, instants[1:], strict=False)
        if since is None or b > since
    ]
    if not gaps:
        return Freshness(None, None, None, margin)
    longest, start, end = max(gaps, key=lambda gap: (gap[0], -gap[1].timestamp()))
    return Freshness(longest, start, end, margin)


@dataclass(frozen=True)
class SchedulePreview:
    """Everything the page's preview and summary show for one schedule.

    ``windows`` are the Family email windows the scheduler would apply over
    the preview (empty when the schedule does not skip around them, or no
    window applies, as in Testing mode).
    """

    days: tuple
    windows: tuple
    cost: Cost
    freshness: Freshness


def summarize(refresh_rules, *, timezone, today, windows, measured, margin):
    """The preview, cost and freshness of ``refresh_rules`` from ``today``.

    Pure; ``schedule_preview`` reads its inputs. The day before ``today`` is
    computed only for the freshness line's first wait.
    """
    days = preview_days(
        refresh_rules,
        timezone=timezone,
        first=today - timedelta(days=1),
        days=DAYS + 1,
        windows=windows,
    )
    shown = days[1:]
    start, end = shown[0].start, shown[-1].end
    applied = tuple(
        window
        for window in windows
        if refresh_rules["skip_around_family_emails"]
        and window.end > start
        and window.start < end
    )
    return SchedulePreview(
        shown,
        applied,
        cost(shown, measured),
        freshness(days, margin, since=start),
    )


def measured_durations(now):
    """``Durations`` from the last seven days' successful runs of each kind.

    A run is a snapshot whose read completed (ready, promoted, or a quick
    update recorded as unchanged, as ``data_age.FACTS_SQL`` counts a
    success), from its start to its completion. Manual refreshes count too,
    as the spec says: the settings page's web login may read only the
    snapshot's public columns (``runtime_grants``), not the attempt that
    links it to its request's cause, and granting more would be a schema
    change. Runs are almost all scheduled (one or more an hour), and a
    median shrugs off the odd manual refresh.
    """
    from .snapshot_models import SourceSnapshot

    samples = {}
    for kind, name in (("full", "full"), ("quick", "delta")):
        rows = SourceSnapshot.objects.filter(
            kind=name,
            state__in=("ready", "promoted", "unchanged"),
            started_at__gte=now - MEASURED_DAYS,
            started_at__lte=now,
            completed_at__isnull=False,
        ).values_list("started_at", "completed_at")
        samples[kind] = [completed - started for started, completed in rows]
    return durations(samples)


def schedule_preview(refresh_rules, *, now, timezone):
    """Read the inputs and return ``summarize``'s ``SchedulePreview``.

    ``timezone`` is the zone refresh times resolve in
    (``data_age.source_timezone``). The windows are the ones the scheduler
    would apply (``send_windows.upcoming_windows``: none outside Production
    mode or while delivery is paused), read only when the rules skip around
    Family emails; the margin is the configured lateness margin. Read-only.
    """
    from parishkit.stewardship.jobs.operational_sources import configured_policy

    from .send_windows import upcoming_windows

    today = now.astimezone(ZoneInfo(timezone)).date()
    windows = ()
    if refresh_rules["skip_around_family_emails"]:
        _, end = _day_bounds(today + timedelta(days=DAYS - 1), timezone)
        windows = tuple(upcoming_windows(now, end))
    return summarize(
        refresh_rules,
        timezone=timezone,
        today=today,
        windows=windows,
        measured=measured_durations(now.astimezone(UTC)),
        margin=timedelta(seconds=configured_policy().source_stale_seconds),
    )
