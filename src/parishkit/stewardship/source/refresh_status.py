"""When ParishSoft data was last fully reloaded, for the Admin pages.

A successful full load is a promoted full snapshot; this includes the load that
finished setup. A failed full refresh leaves its task failed or cancelled,
often before any snapshot exists, so failures come from the refresh tasks.

The incremental ("delta") refreshes are reported beside it: a failed full
reload is far less alarming when the incremental updates are still arriving,
and the banner says which data waits for the next full reload.

The pages also state the data age and connection (#510, ``data_age``):
"ParishSoft data as of" (with the last full refresh when they differ), the
connection line, and which scheduled full refresh is late and by how long.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from django.db import connection

from parishkit.stewardship.jobs.operational_sources import configured_policy

from .cadence import (
    DELTA_REFRESHES,
    FREQUENCIES,
    covered_by_full,
    next_full_at,
    refresh_settings,
)
from .data_age import (
    FACTS_COLUMNS,
    FACTS_SQL,
    Connection,
    connection_state,
    connection_threshold,
    facts_from_row,
    facts_params,
    is_out_of_date,
    overdue_full_slot,
)


@dataclass(frozen=True)
class FullRefreshStatus:
    """The last successful full load, and any failed or running one since."""

    succeeded_at: datetime | None
    failed_at: datetime | None
    running: bool
    # The failed run's task, so the banner links straight to its details.
    failed_task_id: UUID | None = None
    # The latest promoted incremental refresh, and a failed one since it.
    delta_succeeded_at: datetime | None = None
    delta_failed_at: datetime | None = None
    # The configured schedule, when known: "daily", "hourly" or "quarter_hour".
    frequency: str | None = None
    next_full_at: datetime | None = None
    # The configured local full-refresh times (daily frequency) and the
    # incremental cadence: "quarter_hour", "hourly" or "off" (#465).
    full_refresh_times: tuple | None = None
    delta_refresh: str | None = None
    # Data age and connection (#510): when the newest promoted full refresh
    # started, what "data as of" shows, and the connection line.
    full_started_at: datetime | None = None
    data_as_of: datetime | None = None
    connection: Connection | None = None
    # The overdue full slot's due time, when one is due and not yet replaced,
    # and whether it is more than the lateness margin late.
    overdue_at: datetime | None = None
    out_of_date: bool = False
    late_minutes: int | None = None
    # Out of date only because a bulk Family send holds it, within the
    # send's allowance (#510): the pages say "held", not "late", and when
    # it runs at the latest (the scheduler's resume point).
    held_for_send: bool = False
    resume_at: datetime | None = None
    # Held, but the send has ended or the resume point has passed: the
    # catch-up is running or about to, so no "by" time is stated.
    catching_up: bool = False
    # System health's facts (``system_health.HealthFacts``), read in the
    # same statement when the caller asked for them (Home, ADM-13).
    health: object = None

    @property
    def offers_late_run(self):
        """Whether the late-refresh notice carries "Run a full refresh now".

        Only when a scheduled full refresh is past the margin, not held for
        a send, and no full refresh is running already.
        """
        return self.out_of_date and not self.held_for_send and not self.running

    @property
    def shows_full_separately(self):
        """Whether "data as of" differs from the last full refresh's start."""
        return (
            self.full_started_at is not None
            and self.data_as_of is not None
            and self.data_as_of != self.full_started_at
        )

    @property
    def has_deltas(self):
        """Whether separate incremental updates run under this schedule.

        "off" turns them off; otherwise the scheduler's own rule says when
        full refreshes cover every delta slot.
        """
        return self.delta_refresh != "off" and not covered_by_full(
            self.frequency, self.delta_refresh
        )

    @property
    def nightly_only(self):
        """Whether the schedule is one full refresh a day, at the nightly time."""
        return self.frequency == "daily" and len(self.full_refresh_times or ()) <= 1

    @property
    def deltas_healthy(self):
        """Incremental updates arrive; unknown (None) before any has run."""
        if not self.has_deltas:
            # Every run is full, or deltas are off: no incremental updates.
            return None
        if self.delta_failed_at is not None:
            return False
        return True if self.delta_succeeded_at is not None else None


def refresh_schedule(configuration):
    """Read the full-refresh schedule and zone from applied settings.

    Uses only the in-memory canonical document plus the already-loaded current
    campaign, so the Admin home page's query budget is unchanged. Returns None
    when ParishSoft is not configured.
    """
    document = configuration.active_configuration.canonical_document
    sections = document["sections"]
    record = next(
        (
            item["values"]
            for item in sections.get("integrations", [])
            if item["values"].get("kind") == "parishsoft"
        ),
        None,
    )
    if record is None:
        return None
    settings = refresh_settings(record.get("settings", {}))
    campaign = configuration.current_campaign
    timezone = (
        campaign.active_configuration.timezone
        if campaign is not None
        else sections["parish"][0]["values"]["timezone"]
    )
    if (
        settings["frequency"] not in FREQUENCIES
        or settings["delta_refresh"] not in DELTA_REFRESHES
    ):
        return None
    return {"timezone": timezone, **settings}


# The latest full and incremental refresh outcomes: the first half of the
# combined status row, ahead of the ``FACTS_COLUMNS`` data-age facts.
OUTCOMES_SQL = (
    "WITH runs AS (SELECT t.id, t.state, t.updated_at, r.kind "
    "FROM stewardship_task_run t "
    "JOIN stewardship_source_refresh_request r ON r.task_root_id=t.root_id), "
    "failures AS (SELECT id, updated_at FROM runs "
    "WHERE kind='full' AND state IN ('failed','cancelled') "
    "ORDER BY updated_at DESC LIMIT 1) "
    "SELECT (SELECT max(promoted_at) FROM stewardship_source_snapshot "
    "WHERE kind='full' AND state='promoted'), "
    "(SELECT updated_at FROM failures), "
    "EXISTS(SELECT 1 FROM runs WHERE kind='full' AND state='running'), "
    "(SELECT id FROM failures), "
    "(SELECT max(promoted_at) FROM stewardship_source_snapshot "
    "WHERE kind='delta' AND state='promoted'), "
    # Only a failed delta counts: deltas superseded by a newer or full
    # refresh are cancelled routinely and are not a health problem.
    "(SELECT max(updated_at) FROM runs WHERE kind='delta' AND state='failed')"
)


def _facts(row):
    """The data-age facts: the last ``FACTS_COLUMNS`` columns of the row.

    Reading from the end keeps the facts right if the outcome half gains a
    column; a unit test checks both halves' column counts (#659).
    """
    return facts_from_row(row[-FACTS_COLUMNS:])


def full_refresh_status(schedule=None, now=None, *, health=False):
    """Read the latest full and incremental outcomes and the data age.

    The Admin home page has a fixed query budget, so the outcomes and the
    data age and connection facts (``data_age.FACTS_SQL``) come from one
    statement. ``schedule`` (from ``refresh_schedule``) and ``now`` add the
    next scheduled full reload, the overdue full slot (three more queries,
    only once a full refresh has promoted) and the connection line.
    ``health`` joins System health's facts (``system_health.HEALTH_SQL``)
    into the same statement, between the two halves, for Home's problem
    lines (ADM-13); the facts stay last, as ``_facts`` reads them.
    """
    parts = [f"({OUTCOMES_SQL}) outcomes"]
    parameters = facts_params()
    if health:
        from parishkit.stewardship.system_health import HEALTH_SQL, health_params

        parts.append(f"({HEALTH_SQL}) health")
        parameters |= health_params()
    # The data-age facts as the last derived row, so the page pays for one
    # statement, not two (#510).
    parts.append(f"({FACTS_SQL}) facts")
    with connection.cursor() as cursor:
        cursor.execute("SELECT * FROM " + " CROSS JOIN ".join(parts), parameters)
        row = cursor.fetchone()
    succeeded_at, failed_at, running, failed_task_id = row[:4]
    delta_succeeded_at, delta_failed_at = (tuple(row[4:6]) + (None, None))[:2]
    if failed_at is not None and succeeded_at is not None and failed_at < succeeded_at:
        # A later successful full load supersedes an older failure, so the
        # failure banner disappears after the next success of the same kind.
        failed_at = failed_task_id = None
    # Any later successful load, incremental or full, supersedes an older
    # incremental failure: a full reload also carries every recent change.
    latest = max((t for t in (delta_succeeded_at, succeeded_at) if t), default=None)
    if delta_failed_at is not None and latest is not None and delta_failed_at < latest:
        delta_failed_at = None
    facts = _facts(row)
    found_health = None
    if health:
        from parishkit.stewardship.system_health import HEALTH_COLUMNS, health_facts

        # Just before the facts, which are read from the row's end too.
        found_health = health_facts(
            row[-FACTS_COLUMNS - HEALTH_COLUMNS : -FACTS_COLUMNS]
        )
    frequency = next_due = times = delta_refresh = None
    found = overdue = late = resumes = None
    out_of_date = held = catching_up = False
    if schedule is not None and now is not None:
        margin = timedelta(seconds=configured_policy().source_stale_seconds)
        if facts.full_started_at is not None:
            overdue = overdue_full_slot(
                now, after=facts.full_started_at, timezone=schedule["timezone"]
            )
        out_of_date = is_out_of_date(overdue, now, margin)
        if overdue is not None:
            late = max(0, int((now - overdue).total_seconds()) // 60)
        # Imported lazily: the send checks pull in the mail and schedule
        # models, and they run only when the data is out of date or the
        # connection gap is exceeded.
        from .health import failed_since
        from .send_hold import allowance_applies, family_send_active
        from .send_hold import resume_at as resume_point

        if out_of_date and allowance_applies(overdue, now, failed_since=failed_since):
            held, resumes = True, resume_point(overdue)
            catching_up = now >= resumes or not family_send_active()

        found = connection_state(
            facts,
            now=now,
            threshold=connection_threshold(schedule, margin),
            sending=family_send_active,
        )
        frequency = schedule["frequency"]
        times = tuple(schedule["full_refresh_times"])
        delta_refresh = schedule["delta_refresh"]
        next_due = next_full_at(
            now=now,
            timezone=schedule["timezone"],
            nightly_time=schedule["nightly_time"],
            frequency=frequency,
            full_refresh_times=times,
        )
    return FullRefreshStatus(
        succeeded_at,
        failed_at,
        running,
        failed_task_id,
        delta_succeeded_at,
        delta_failed_at,
        frequency,
        next_due,
        times,
        delta_refresh,
        facts.full_started_at,
        facts.data_as_of,
        found,
        overdue,
        out_of_date,
        late,
        held,
        resumes,
        catching_up,
        found_health,
    )
