"""When ParishSoft data was last fully reloaded, for the Admin pages.

A successful full load is a promoted full snapshot; this includes the load that
finished setup. A failed full refresh leaves its task failed or cancelled,
often before any snapshot exists, so failures come from the refresh tasks.

The 15-minute incremental ("delta") refreshes are reported beside it: a failed
nightly full reload is far less alarming when the incremental updates are
still arriving, and the banner says which data waits for the next full reload.
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from django.db import connection

from .cadence import FREQUENCIES, next_full_at


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

    @property
    def deltas_healthy(self):
        """Incremental updates arrive; unknown (None) before any has run."""
        if self.frequency == "quarter_hour":
            # Every run is full, so there are no separate incremental updates.
            return None
        if self.delta_failed_at is not None:
            return False
        return True if self.delta_succeeded_at is not None else None


def refresh_schedule(configuration):
    """Read the full-refresh frequency, time and zone from applied settings.

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
    settings = record.get("settings", {})
    campaign = configuration.current_campaign
    timezone = (
        campaign.active_configuration.timezone
        if campaign is not None
        else sections["parish"][0]["values"]["timezone"]
    )
    frequency = settings.get("full_refresh", "daily")
    if frequency not in FREQUENCIES:
        return None
    return {
        "timezone": timezone,
        "nightly_time": settings.get("nightly_time", "02:00"),
        "frequency": frequency,
    }


def full_refresh_status(schedule=None, now=None):
    """Read the latest full and incremental outcomes in one query.

    The Admin home page has a fixed query budget, so every fact comes from a
    scalar subquery of a single statement. ``schedule`` (from
    ``refresh_schedule``) and ``now`` add the next scheduled full reload.
    """
    with connection.cursor() as cursor:
        cursor.execute(
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
    frequency = next_due = None
    if schedule is not None and now is not None:
        frequency = schedule["frequency"]
        next_due = next_full_at(now=now, **schedule)
    return FullRefreshStatus(
        succeeded_at,
        failed_at,
        running,
        failed_task_id,
        delta_succeeded_at,
        delta_failed_at,
        frequency,
        next_due,
    )
