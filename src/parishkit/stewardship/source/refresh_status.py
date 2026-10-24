"""When ParishSoft data was last fully reloaded, for the Admin pages.

A successful full load is a promoted full snapshot; this includes the load that
finished setup. A failed full refresh leaves its task failed or cancelled,
often before any snapshot exists, so failures come from the refresh tasks.

The incremental ("delta") refreshes, or quick updates, were reported beside
it until they were removed (#1147).

The pages also state the data age and connection (#510, ``data_age``):
"ParishSoft data as of", the connection line, and which scheduled full
refresh is late and by how long.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from django.db import connection

from parishkit.stewardship.jobs.operational_sources import configured_policy

from .cadence import FREQUENCIES, next_full_at, refresh_settings, skips_around_emails
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
    # The configured schedule, when known: "daily", "hourly" or "quarter_hour".
    frequency: str | None = None
    next_full_at: datetime | None = None
    # The configured local full-refresh times (daily frequency, #465).
    full_refresh_times: tuple | None = None
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
    def nightly_only(self):
        """Whether the schedule is one full refresh a day, at the nightly time."""
        return self.frequency == "daily" and len(self.full_refresh_times or ()) <= 1


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
    if settings["frequency"] not in FREQUENCIES:
        return None
    # Whether the schedule skips refreshes around Family emails (#632): the
    # rules themselves are not kept in ``settings``.
    return {
        "timezone": timezone,
        **settings,
        "skips_around_emails": skips_around_emails(record.get("settings", {})),
    }


# The latest full refresh outcomes: the first half of the
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
    "(SELECT id FROM failures)"
)


def _facts(row):
    """The data-age facts: the last ``FACTS_COLUMNS`` columns of the row.

    Reading from the end keeps the facts right if the outcome half gains a
    column; a unit test checks both halves' column counts (#659).
    """
    return facts_from_row(row[-FACTS_COLUMNS:])


def full_refresh_status(schedule=None, now=None, *, health=False):
    """Read the latest full refresh outcomes and the data age.

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
    if failed_at is not None and succeeded_at is not None and failed_at < succeeded_at:
        # A later successful full load supersedes an older failure, so the
        # failure banner disappears after the next success of the same kind.
        failed_at = failed_task_id = None
    facts = _facts(row)
    found_health = None
    if health:
        from parishkit.stewardship.system_health import HEALTH_COLUMNS, health_facts

        # Just before the facts, which are read from the row's end too.
        found_health = health_facts(
            row[-FACTS_COLUMNS - HEALTH_COLUMNS : -FACTS_COLUMNS]
        )
    frequency = next_due = times = None
    found = overdue = late = resumes = None
    out_of_date = held = catching_up = False
    if schedule is not None and now is not None:
        margin = timedelta(seconds=configured_policy().source_stale_seconds)
        if facts.full_started_at is not None:
            overdue = overdue_full_slot(
                now, after=facts.full_started_at, timezone=schedule["timezone"]
            )
            # A slot due during a go-live's refresh hold counts from the
            # hold's end, as the stale alarm counts it (#462).
            from parishkit.stewardship.campaigns.go_live_sequencing import (
                held_until,
            )

            overdue = held_until(overdue)
        out_of_date = is_out_of_date(overdue, now, margin)
        if overdue is not None:
            late = max(0, int((now - overdue).total_seconds()) // 60)
        # Imported lazily: the send checks pull in the mail and schedule
        # models, and they run only when the data is out of date or the
        # connection gap is exceeded.
        from parishkit.stewardship.campaigns.go_live_sequencing import last_hold_end
        from parishkit.stewardship.campaigns.go_live_sequencing import (
            refreshes_held as go_live_holding,
        )

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
            sending=lambda: family_send_active() or go_live_holding(now),
            held=lambda: last_hold_end(now),
        )
        frequency = schedule["frequency"]
        times = tuple(schedule["full_refresh_times"])
        next_due = next_full_at(
            now=now,
            timezone=schedule["timezone"],
            nightly_time=schedule["nightly_time"],
            frequency=frequency,
            full_refresh_times=times,
            rules=schedule.get("rules", False),
        )
    return FullRefreshStatus(
        succeeded_at,
        failed_at,
        running,
        failed_task_id,
        frequency,
        next_due,
        times,
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
