"""Render the real System health templates with sample states (ADM-13, #530)."""

from datetime import timedelta

from django.template.loader import render_to_string

from parishkit.stewardship.accounts.system_health_views import POLL_MILLISECONDS
from parishkit.stewardship.source.data_age import Connection
from parishkit.stewardship.source.refresh_status import FullRefreshStatus
from parishkit.stewardship.system_health import (
    BackupRequestStatus,
    DropCount,
    SystemHealth,
    find_problems,
    group_processes,
)

PAGE = "/system-health"
HEALTHY = "/system-health-healthy"
STATUS = "/system-health/status"
# A healthy page whose polls bring the same state again.
STEADY = "/system-health-steady"
STEADY_STATUS = "/system-health-steady/status"
# A healthy page whose next poll changes only a time's datetime attribute.
DATED = "/system-health-dated"
DATED_STATUS = "/system-health-dated/status"


def _row(now, service, process="main", *, age=0, **values):
    """One status row that last reported ``age`` seconds before ``now``."""
    sender = service == "mail-dispatch"
    return {
        "service": service,
        "process": process,
        "target": None,
        "started_at": now - timedelta(hours=5),
        "reported_at": now - timedelta(seconds=age),
        "application_version": "1.4.2",
        "debug_logging": False,
        "sender_state": "running" if sender else None,
        "sender_since": now - timedelta(hours=5) if sender else None,
        "sender_until": None,
    } | values


def health(now, *, troubled, backup_at=None):
    """A deployment with several problems, or a healthy one."""
    rows = [
        _row(now, "web"),
        _row(now, "web"),
        _row(now, "worker"),
        _row(now, "worker", "source"),
        _row(now, "scheduler", age=600 if troubled else 0),
        _row(
            now,
            "mail-dispatch",
            sender_state="halted" if troubled else "running",
            sender_since=now - timedelta(minutes=12),
        ),
        _row(
            now,
            "mail-dispatch",
            "mail",
            sender_state="gmail_held" if troubled else "running",
            sender_until=now + timedelta(minutes=40) if troubled else None,
        ),
    ]
    processes = group_processes(rows, now)
    incidents = (
        {"source_destructive_change": now - timedelta(hours=3)} if troubled else {}
    )
    refresh = FullRefreshStatus(
        now - timedelta(hours=9),
        None,
        False,
        full_started_at=now - timedelta(hours=9),
        data_as_of=now - timedelta(hours=9),
        connection=Connection("working", now - timedelta(minutes=10)),
    )
    problems = find_problems(
        mode="production",
        processes=processes,
        missing=(),
        schema_current=True,
        incidents=incidents,
        refresh=refresh,
        backup_at=now - timedelta(hours=13),
    )
    return SystemHealth(
        checked_at=now,
        mode="production",
        problems=problems,
        processes=processes,
        missing_services=(),
        versions=("1.4.2",),
        schema_current=True,
        delivery_paused=troubled,
        paused_at=now - timedelta(minutes=30) if troubled else None,
        send_active=False,
        planning_held=False,
        retry_waiting=4 if troubled else 0,
        next_retry_at=now + timedelta(minutes=3) if troubled else None,
        incidents=incidents,
        refresh=refresh,
        refused_at=now - timedelta(hours=3) if troubled else None,
        refused_counts=(
            DropCount("family", 1090, 1084, 25, False),
            DropCount("email_eligible_families", 1084, 612, 25, True),
        )
        if troubled
        else (),
        backup_at=backup_at or now - timedelta(hours=13),
        backup_key_matches=True,
        backup_bytes=48 * 1024 * 1024,
        backup_version="1.4.2",
        backup_request=BackupRequestStatus(
            state="waiting",
            status="held",
            created_at=now - timedelta(minutes=12),
            held_at=now - timedelta(minutes=2),
            claimed_at=None,
            finished_at=None,
            failure_kind=None,
        )
        if troubled
        else None,
        offsite=None,
    )


def components(context, admin):
    """The troubled page (polling a healthy fragment) and the healthy page."""
    now = context["server_now"]
    chrome = admin | {
        "sections": [
            {
                "key": "system",
                "label": "System",
                "current": True,
                "items": [{"url": PAGE, "label": "System health", "current": "page"}],
            }
        ],
        "breadcrumbs": [
            {"label": "Home", "url": "/home"},
            {"label": "System", "url": PAGE},
            {"label": "System health", "url": None},
        ],
    }

    def values(troubled, backup_at=None, **extra):
        """Template context the view would build."""
        return (
            context
            | {
                "admin_chrome": chrome,
                "health": health(now, troubled=troubled, backup_at=backup_at),
                "campaign_id": None,
                "pause": {"actor": "admin@example.org", "reason": "Checking a typo"}
                if troubled
                else None,
                "poll_interval": POLL_MILLISECONDS,
            }
            | extra
        )

    return {
        PAGE: (
            "text/html",
            render_to_string(
                "stewardship/system-health.html", values(True, status_url=STATUS)
            ),
        ),
        HEALTHY: (
            "text/html",
            render_to_string("stewardship/system-health.html", values(False)),
        ),
        STEADY: (
            "text/html",
            render_to_string(
                "stewardship/system-health.html",
                values(False, status_url=STEADY_STATUS),
            ),
        ),
        STEADY_STATUS: (
            "text/html",
            render_to_string("stewardship/system-health-status.html", values(False)),
        ),
        DATED: (
            "text/html",
            render_to_string(
                "stewardship/system-health.html",
                values(False, status_url=DATED_STATUS),
            ),
        ),
        # A newer backup finished: only the times' datetime attributes change.
        DATED_STATUS: (
            "text/html",
            render_to_string(
                "stewardship/system-health-status.html",
                values(False, backup_at=now - timedelta(hours=1)),
            ),
        ),
        # What the troubled page's next poll reads: every problem has ended.
        STATUS: (
            "text/html",
            render_to_string("stewardship/system-health-status.html", values(False)),
        ),
    }
