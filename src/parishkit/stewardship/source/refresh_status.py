"""When ParishSoft data was last fully reloaded, for the Admin pages.

A successful full load is a promoted full snapshot; this includes the load that
finished setup. A failed full refresh leaves its task failed or cancelled,
often before any snapshot exists, so failures come from the refresh tasks.
"""

from dataclasses import dataclass
from datetime import datetime

from django.db import connection


@dataclass(frozen=True)
class FullRefreshStatus:
    """The last successful full load, and any failed or running one since."""

    succeeded_at: datetime | None
    failed_at: datetime | None
    running: bool


def full_refresh_status():
    """Read the latest full-load outcomes in one query, without source payloads.

    The Admin home page has a fixed query budget, so the three facts come
    from scalar subqueries of a single statement.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            "WITH runs AS (SELECT t.state, t.updated_at FROM stewardship_task_run t "
            "JOIN stewardship_source_refresh_request r ON r.task_root_id=t.root_id "
            "WHERE r.kind='full') "
            "SELECT (SELECT max(promoted_at) FROM stewardship_source_snapshot "
            "WHERE kind='full' AND state='promoted'), "
            "(SELECT max(updated_at) FROM runs "
            "WHERE state IN ('failed','cancelled')), "
            "EXISTS(SELECT 1 FROM runs WHERE state='running')"
        )
        succeeded_at, failed_at, running = cursor.fetchone()
    if failed_at is not None and succeeded_at is not None and failed_at < succeeded_at:
        # A later success supersedes an older failure.
        failed_at = None
    return FullRefreshStatus(succeeded_at, failed_at, running)
