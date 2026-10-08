"""ParishSoft data age, the out-of-date rule and the connection line (#510).

"Fresh ParishSoft data" used to mean only that ParishSoft had answered within
``source_stale_seconds``: an empty quick update promoted a snapshot and reset
the clock, while ParishSoft's change list returned nothing. Three facts replace
it (see the operations spec, "ParishSoft data age and connection"):

- **Last full refresh:** the start of the newest promoted full refresh. Only
  this drives the ``source_stale`` alarm and the bulk-send hold, because only
  a full refresh re-reads everything.
- **Data as of:** what pages and the digest show: the last full refresh, or a
  later quick update whose snapshot cursor counted at least one change.
- **Connection:** whether ParishSoft answers, from refresh attempts only.

The data is **out of date** when the **overdue full slot** (the first counted
scheduled full due time after the last full refresh started) is more than the
lateness margin (``source_stale_seconds``) late. A due time counts only after
the schedule it belongs to first became effective, so saving a schedule never
alarms at once, yet a slot that was already overdue under an earlier schedule
stays overdue until a full refresh promotes.

The pure functions here take every input as an argument, so they are unit
tested without a database; the readers below them feed them from durable
records that the web, scheduler and worker logins can already read.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.db import connection

from parishkit.stewardship.accounts.bootstrap_schema import BOOTSTRAP_SCHEMA
from parishkit.stewardship.campaigns.intervals import resolve_local

from .cadence import (
    HOUR,
    QUARTER_HOUR,
    longest_gap,
    refresh_settings,
    skips_around_emails,
)

# The scheduler's durable record of a held full slot (``production._log_skip``)
# uses this event with the task-free ``schedule`` context schema; a refresh
# task's own held retry uses the ``task`` schema and is never send-hold
# evidence.
HELD_EVENT = "source_refresh_held"
HELD_SCHEMA = "schedule"


@dataclass(frozen=True)
class Connection:
    """The connection line: the first state that applies, and its time.

    ``state`` is "failing" (since the earliest failed attempt after the newest
    success), "not_checked" (since the newest success), "working" (last
    answered at) or "unknown" (no attempt has finished yet; no time).
    """

    state: str
    at: datetime | None = None


@dataclass(frozen=True)
class SourceFacts:
    """Durable refresh facts the pages, the digest and the alarm share.

    Every time is a snapshot's start except ``answered_at`` (when ParishSoft
    last finished answering) and ``held_at`` (the newest scheduler send-hold
    entry since the newest success).
    """

    full_started_at: datetime | None = None
    changed_delta_at: datetime | None = None
    success_at: datetime | None = None
    answered_at: datetime | None = None
    newest_failed: bool = False
    failing_since: datetime | None = None
    held_at: datetime | None = None

    @property
    def data_as_of(self):
        """The last full refresh, or a later quick update that changed data."""
        times = [t for t in (self.full_started_at, self.changed_delta_at) if t]
        return max(times, default=None)


def full_due_after(settings, timezone, after, until):
    """The earliest scheduled full due time in ``(after, until]``, or None.

    ``settings`` is ``cadence.refresh_settings`` output. Listed daily times
    are parish-local wall times resolved through the shared daylight-saving
    resolver, exactly as the scheduler resolves them; a legacy hourly or
    quarter-hour full refresh falls on every UTC hour or quarter hour.
    """
    if until <= after:
        return None
    after, until = after.astimezone(UTC), until.astimezone(UTC)
    frequency = settings["frequency"]
    if frequency != "daily":
        step = HOUR if frequency == "hourly" else QUARTER_HOUR
        minutes = int(step.total_seconds()) // 60
        floor = after.replace(
            minute=after.minute // minutes * minutes, second=0, microsecond=0
        )
        due = floor + step
        return due if due <= until else None
    zone = ZoneInfo(timezone)
    walls = [time.fromisoformat(value) for value in settings["full_refresh_times"]]
    # Start a day early: a wall time late on the previous local day can
    # resolve after ``after`` around a daylight-saving change.
    day = after.astimezone(zone).date() - timedelta(days=1)
    last = until.astimezone(zone).date() + timedelta(days=1)
    while day <= last:
        due = [
            instant
            for instant in (
                resolve_local(datetime.combine(day, wall), timezone) for wall in walls
            )
            if after < instant <= until
        ]
        if due:
            return min(due)
        day += timedelta(days=1)
    return None


def first_overdue(runs, timezone, after, now, skipped=frozenset()):
    """The overdue full slot: the first counted full due time after ``after``.

    ``runs`` lists each schedule in effect, oldest first, as ``(start,
    settings)``: ``settings`` applies from ``start`` until the next run's
    start (the last run until ``now``); ``None`` settings schedule nothing.
    A due time counts only if it falls after its own schedule took effect, so
    a new schedule's earlier times are ignored, while an earlier schedule's
    slot that was already due stays overdue. A due instant in ``skipped``
    (full slots recorded as skipped around a Family email, #632) was never
    due and does not count.
    """
    for index, (start, settings) in enumerate(runs):
        end = runs[index + 1][0] if index + 1 < len(runs) else now
        lower = max(start, after)
        if settings is None or end <= lower:
            continue
        due = full_due_after(settings, timezone, lower, min(end, now))
        while due is not None and due in skipped:
            due = full_due_after(settings, timezone, due, min(end, now))
        if due is not None:
            return due
    return None


def is_out_of_date(overdue, now, margin):
    """Whether the overdue full slot is more than ``margin`` late."""
    return overdue is not None and now - overdue >= margin


def changed(cursor):
    """Whether a snapshot cursor's ``changes`` counted at least one change.

    The counts are display-only and absent when counting failed; absent,
    malformed or all-zero counts are "no change".
    """
    changes = cursor.get("changes") if type(cursor) is dict else None
    return type(changes) is dict and any(
        type(value) is int and value > 0 for value in changes.values()
    )


def connection_threshold(settings, margin):
    """How long without a successful attempt reads as "not checked".

    The schedule's longest gap between any two scheduled refreshes, listed
    quick times included (#632), plus the lateness margin.
    """
    return (
        longest_gap(
            frequency=settings["frequency"],
            full_refresh_times=settings["full_refresh_times"],
            delta_refresh=settings["delta_refresh"],
            quick_refresh_times=settings.get("quick_refresh_times", ()),
        )
        + margin
    )


def connection_state(
    facts, *, now, threshold, sending=lambda: False, held=lambda: None
):
    """The connection line from ``facts``: failing, not checked or working.

    Only finished refresh attempts that called ParishSoft count; held or
    skipped slots and superseded runs leave no attempt. Time spent holding
    for a send does not count toward "not checked": the gap is measured from
    the newer of the last success and the scheduler's newest durable hold
    entry, and while a send is in progress (``sending``, called only when the
    gap is exceeded) the line never reads "not checked". ``held``, also
    called only when the gap is exceeded, returns the end of the latest
    go-live refresh hold (#462) or None; the gap counts from it too, so the
    line does not read "not checked" right after a hold ends.
    """
    if facts.newest_failed and facts.failing_since is not None:
        return Connection("failing", facts.failing_since)
    if facts.success_at is None:
        return Connection("unknown")
    since = max(t for t in (facts.success_at, facts.held_at) if t)
    if threshold is not None and now - since > threshold:
        # Read the go-live hold only when the gap is exceeded: it counts like
        # the scheduler's newest durable send-hold entry.
        ended = held()
        if (ended is None or now - ended > threshold) and not sending():
            return Connection("not_checked", facts.success_at)
    return Connection("working", facts.answered_at or facts.success_at)


# One statement for every snapshot fact, so a page adds a single query. A
# success is a snapshot whose read completed (ready, promoted, or a quick
# update recorded as unchanged, #630); a failed attempt is one rejected after
# it called ParishSoft. An unchanged quick update never moves "data as of".
# ``%(as_of)s`` limits the facts to what was known at a past instant (the
# digest's observation).
FACTS_SQL = (
    "WITH s AS (SELECT kind, state, started_at, completed_at, cursor "
    "FROM stewardship_source_snapshot "
    "WHERE state IN ('ready','promoted','unchanged','rejected') "
    "AND (%(as_of)s::timestamptz IS NULL OR started_at<=%(as_of)s)), "
    "ok AS (SELECT started_at, completed_at FROM s WHERE state<>'rejected' "
    "ORDER BY started_at DESC LIMIT 1) "
    "SELECT (SELECT max(started_at) FROM s WHERE kind='full' AND state='promoted'), "
    "(SELECT max(started_at) FROM s WHERE kind='delta' AND state='promoted' "
    "AND jsonb_typeof(cursor->'changes')='object' AND EXISTS ("
    "SELECT 1 FROM jsonb_each(cursor->'changes') c "
    "WHERE jsonb_typeof(c.value)='number' AND (c.value)::numeric>0)), "
    "(SELECT started_at FROM ok), (SELECT completed_at FROM ok), "
    "(SELECT state FROM s ORDER BY started_at DESC LIMIT 1)='rejected', "
    "(SELECT min(started_at) FROM s WHERE state='rejected' "
    "AND started_at>coalesce((SELECT started_at FROM ok),'-infinity')), "
    "(SELECT max(created_at) FROM stewardship_operational_log "
    "WHERE level='INFO' AND event=%(event)s AND schema=%(schema)s "
    "AND created_at>coalesce((SELECT started_at FROM ok),'-infinity') "
    "AND (%(as_of)s::timestamptz IS NULL OR created_at<=%(as_of)s))"
)
# How many columns ``FACTS_SQL`` selects, one per ``SourceFacts`` field. The
# Admin refresh status appends them to its own outcome columns and reads them
# from the end of the combined row by this count (#659).
FACTS_COLUMNS = 7


def facts_from_row(row):
    """Build ``SourceFacts`` from the ``FACTS_COLUMNS`` ``FACTS_SQL`` selects."""
    return SourceFacts(
        full_started_at=row[0],
        changed_delta_at=row[1],
        success_at=row[2],
        answered_at=row[3],
        newest_failed=bool(row[4]),
        failing_since=row[5],
        held_at=row[6],
    )


def facts_params(as_of=None):
    """The named parameters ``FACTS_SQL`` takes."""
    return {"as_of": as_of, "event": HELD_EVENT, "schema": HELD_SCHEMA}


def source_facts(as_of=None):
    """Read the shared snapshot and hold facts in one query."""
    with connection.cursor() as cursor:
        cursor.execute(FACTS_SQL, facts_params(as_of))
        return facts_from_row(cursor.fetchone())


def last_full_started_at():
    """The start of the newest promoted full refresh, or None."""
    from .snapshot_models import SourceSnapshot

    return (
        SourceSnapshot.objects.filter(kind="full", state="promoted")
        .order_by("-started_at")
        .values_list("started_at", flat=True)
        .first()
    )


def schedule_runs(after):
    """The schedules in effect from ``after`` on, oldest first.

    Each run is ``(start, settings)``: the activation of the earliest
    configuration in an unbroken run of activations whose refresh schedule
    settings are equal, found the way ``health.initial_source_at`` finds a
    tenant's first activation (bootstrap configurations are ignored). Other
    configuration changes do not start a new run. The first run is the one in
    effect at ``after``; its start may be earlier, which ``first_overdue``
    clamps to ``after`` anyway, so no older activation is read. Three queries.
    """
    from parishkit.stewardship.accounts.configuration_models import (
        AppliedIntegration,
    )
    from parishkit.stewardship.accounts.runtime_models import (
        ConfigurationActivation,
    )

    activations = ConfigurationActivation.objects.exclude(
        configuration__validation_schema=BOOTSTRAP_SCHEMA
    ).values_list("created_at", "configuration_id")
    rows = list(activations.filter(created_at__gt=after).order_by("sequence"))
    prior = activations.filter(created_at__lte=after).order_by("-sequence").first()
    if prior is not None:
        rows.insert(0, prior)
    stored = dict(
        AppliedIntegration.objects.filter(
            configuration_id__in=[value for _, value in rows], kind="parishsoft"
        ).values_list("configuration_id", "settings")
    )
    runs = []
    for created_at, configuration_id in rows:
        value = stored.get(configuration_id)
        value = None if value is None else refresh_settings(value)
        if runs and runs[-1][1] == value:
            continue
        runs.append((created_at, value))
    return runs


def skip_setting_since():
    """When "skip refreshes around Family emails" last took effect, or None.

    The activation of the earliest configuration in the latest unbroken run
    of activations whose ParishSoft schedule skips around Family emails
    (bootstrap configurations ignored), found the way ``schedule_runs``
    finds a schedule's start (#632). None when the active configuration does
    not skip. Slots due before it are never decided as skipped, so turning
    the setting on cannot turn an already overdue slot into a skip.
    """
    from django.db.models import OuterRef, Subquery

    from parishkit.stewardship.accounts.configuration_models import (
        AppliedIntegration,
    )
    from parishkit.stewardship.accounts.runtime_models import (
        ConfigurationActivation,
    )

    settings = AppliedIntegration.objects.filter(
        configuration_id=OuterRef("configuration_id"), kind="parishsoft"
    ).values("settings")[:1]
    rows = (
        ConfigurationActivation.objects.exclude(
            configuration__validation_schema=BOOTSTRAP_SCHEMA
        )
        .annotate(settings=Subquery(settings))
        .order_by("-sequence")
        .values_list("created_at", "settings")
    )
    since = None
    for created_at, value in rows.iterator(chunk_size=50):
        if not (type(value) is dict and skips_around_emails(value)):
            break
        since = created_at
    return since


def source_timezone(active_configuration_id):
    """The zone refresh times resolve in: the current campaign's, else the parish's.

    The scheduler uses the same rule when it creates slots.
    """
    from parishkit.stewardship.accounts.configuration_models import Parish
    from parishkit.stewardship.accounts.runtime_models import SystemConfiguration

    zone = (
        SystemConfiguration.objects.filter(current_campaign__isnull=False)
        .values_list("current_campaign__active_configuration__timezone", flat=True)
        .first()
    )
    if zone is not None:
        return zone
    return Parish.objects.values_list("timezone", flat=True).get(
        configuration_id=active_configuration_id
    )


def skipped_full_after(after):
    """The due instants of full slots recorded as skipped after ``after`` (#632).

    Read from the slot decision record; a recorded skip was never due, so
    the alarm ignores it even if the windows have changed since.
    """
    from .refresh_models import SourceSlotDecision

    return frozenset(
        SourceSlotDecision.objects.filter(
            cause="nightly", decision="skipped", due_at__gt=after
        ).values_list("due_at", flat=True)
    )


def overdue_full_slot(now, *, after, timezone):
    """The overdue full slot after ``after`` under the recorded schedules."""
    return first_overdue(
        schedule_runs(after), timezone, after, now, skipped_full_after(after)
    )


def current_overdue(now):
    """``(overdue slot, last full start)`` for the active configuration.

    The slot is None when no full refresh has promoted yet or no
    configuration is active: there is no data to call out of date (the alarm
    has its own initial grace for that case).
    """
    from parishkit.stewardship.accounts.runtime_models import SystemConfiguration

    last_full = last_full_started_at()
    active = SystemConfiguration.objects.values_list(
        "active_configuration_id", flat=True
    ).first()
    if last_full is None or active is None:
        return None, last_full
    return (
        overdue_full_slot(now, after=last_full, timezone=source_timezone(active)),
        last_full,
    )


def catch_up_at(timezone):
    """When the current schedule took effect, if it must request a catch-up.

    The schedule-change catch-up (#632): when a new schedule takes effect
    while a full slot of an earlier one is already overdue (due, and no full
    refresh that started at or after its due time has promoted, whether or
    not the margin has run out), the new schedule may never create that
    slot, so the scheduler requests one full refresh due at this instant
    instead. None when no full refresh has promoted yet, when the schedule
    has not changed since the last one started, or when nothing was overdue
    as it changed. Once a full refresh promotes after the overdue slot, this
    returns None again. ``timezone`` is the zone the slots resolve in. Five
    queries.
    """
    last_full = last_full_started_at()
    if last_full is None:
        return None
    runs = schedule_runs(last_full)
    if len(runs) < 2 or runs[-1][1] is None:
        return None
    start = runs[-1][0]
    skipped = skipped_full_after(last_full)
    if first_overdue(runs[:-1], timezone, last_full, start, skipped) is None:
        return None
    return start


@dataclass(frozen=True)
class DataAge:
    """What the daily digest states: data as of, the last full refresh, connection."""

    data_as_of: datetime | None
    full_started_at: datetime | None
    connection: Connection

    def __post_init__(self):
        """Refuse anything but aware instants and a connection line."""
        if not isinstance(self.connection, Connection) or any(
            value is not None
            and (type(value) is not datetime or value.utcoffset() is None)
            for value in (self.data_as_of, self.full_started_at, self.connection.at)
        ):
            raise ValueError("Data age requires aware instants and a connection.")


def data_age_at(as_of):
    """The data age and connection around ``as_of``, for the daily digest.

    Considers snapshots that started by then and scheduler hold entries
    written by then. Each snapshot's state is read as it is now (one still
    running then may have finished since), and the "not checked" gap and the
    send check use the current schedule and sends, so a page that recomputes
    this later can differ slightly from the email, whose compiled body is
    retained. Two queries, plus the send check when the gap is exceeded.
    """
    from parishkit.stewardship.accounts.configuration_models import (
        AppliedIntegration,
    )
    from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
    from parishkit.stewardship.campaigns.go_live_sequencing import last_hold_end
    from parishkit.stewardship.campaigns.go_live_sequencing import (
        refreshes_held as go_live_holding,
    )
    from parishkit.stewardship.jobs.operational_sources import configured_policy

    from .send_hold import family_send_active

    facts = source_facts(as_of=as_of)
    stored = (
        AppliedIntegration.objects.filter(
            configuration_id__in=SystemConfiguration.objects.values(
                "active_configuration_id"
            ),
            kind="parishsoft",
        )
        .values_list("settings", flat=True)
        .first()
    )
    margin = timedelta(seconds=configured_policy().source_stale_seconds)
    threshold = (
        None
        if stored is None
        else connection_threshold(refresh_settings(stored), margin)
    )
    return DataAge(
        facts.data_as_of,
        facts.full_started_at,
        connection_state(
            facts,
            now=as_of,
            threshold=threshold,
            # The go-live reads use the request as it stands now, not as
            # of ``as_of``: a digest observed during a hold may still read
            # "not checked" once that request has moved on (#462).
            sending=lambda: family_send_active() or go_live_holding(as_of),
            held=lambda: last_hold_end(as_of),
        ),
    )
