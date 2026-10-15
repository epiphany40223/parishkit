"""Render the real System health templates with sample states (ADM-13, #530)."""

from datetime import timedelta
from types import SimpleNamespace
from uuid import UUID

from django.template.loader import render_to_string
from django.utils.http import urlencode

from parishkit.stewardship.accounts.system_health_views import POLL_MILLISECONDS
from parishkit.stewardship.source.data_age import Connection
from parishkit.stewardship.source.refresh_status import FullRefreshStatus
from parishkit.stewardship.system_health import (
    AcceptPreview,
    BackupPreview,
    BackupRequestStatus,
    DebugAllowPreview,
    DebugOffPreview,
    DebugSwitchStatus,
    DropAcceptanceStatus,
    DropCount,
    SystemHealth,
    dashboard,
    find_problems,
    group_processes,
    loaded_query,
    loaded_states,
    page_findings,
)

from .automation_components import canonical
from .go_live_components import RESAVE

PAGE = "/system-health"
HEALTHY = "/system-health-healthy"
STATUS = "/system-health/status"
# A healthy page whose polls bring the same state again.
STEADY = "/system-health-steady"
STEADY_STATUS = "/system-health-steady/status"
# Take a backup now (ADM-13 PR 3b): the page, and the answers its preview and
# confirmation POSTs get (the browser test routes the POSTs to them).
# The page is served at its real address, so the answers (at the same
# address in the real app) are swapped in place rather than shown as a
# page from elsewhere.
BACKUP_PAGE = "/admin/system/health/"
BACKUP_STATUS = "/system-health-backup/status"
BACKUP_PREVIEW = "/system-health-backup-preview"
BACKUP_REQUESTED = "/system-health-backup-requested"
BACKUP_STEP_UP = "/system-health-backup-step-up"
BACKUP_BUSY = "/system-health-backup-busy"
# Turn off debug logging (ADM-13 PR 6b): a page whose web portal reports
# debug logging, and the answers its steps get. The browser test serves the
# page at the real address (BACKUP_PAGE) by routing, as the POSTs are.
DEBUG_PAGE = "/system-health-debug"
# The next poll: another Administrator has set the switch.
DEBUG_STATUS = "/system-health-debug/status"
DEBUG_PREVIEW = "/system-health-debug-preview"
DEBUG_SET = "/system-health-debug-set"
DEBUG_STEP_UP = "/system-health-debug-step-up"
# Accept this change once (ADM-13 PR 5b): a page showing only a refused
# large change, the answers its steps get, and its next poll, in which
# another Administrator has accepted it. The browser test serves the page at
# the real address (BACKUP_PAGE) by routing, as the POSTs are.
ACCEPT_PAGE = "/system-health-accept"
ACCEPT_STATUS = "/system-health-accept/status"
ACCEPT_PREVIEW = "/system-health-accept-preview"
ACCEPT_DONE = "/system-health-accept-done"
ACCEPT_CHANGED = "/system-health-accept-changed"
# A page drawn before any refusal, whose next poll brings one: the empty
# kept element takes the Accept this change once region from the poll.
ACCEPT_LATE = "/system-health-accept-late"
# A page whose next poll shows a newer refusal than the one an open preview
# reviews, so the preview is marked changed.
ACCEPT_STALE = "/system-health-accept-stale"
ACCEPT_STALE_STATUS = "/system-health-accept-stale/status"
ACCEPT_LATE_STATUS = "/system-health-accept-late/status"
# Allow debug logging again (ADM-13 PR 6c): a Testing page with the switch
# set, its poll (unchanged), and the answers its steps get.
ALLOW_PAGE = "/system-health-allow"
ALLOW_STATUS = "/system-health-allow/status"
ALLOW_PREVIEW = "/system-health-allow-preview"
ALLOW_CLEARED = "/system-health-allow-cleared"
# A healthy page whose next poll changes only a time's datetime attribute.
DATED = "/system-health-dated"
DATED_STATUS = "/system-health-dated/status"
# A healthy page whose status checks the browser test makes fail (Not
# current, #1214), and the troubled state the same page's next good check
# brings.
STALE = "/system-health-stale"
STALE_STATUS = "/system-health-stale/status"
# The troubled page's page-load tiles (#1214): its re-save list has an item
# that cannot be sent, so that tile is an Error tile, and the page passes
# that to its polled status as ?loaded=, which the fragment counts.
LOADED = loaded_states(0, list(RESAVE))
TROUBLED_STATUS = canonical(f"{STATUS}?{urlencode({'loaded': loaded_query(LOADED)})}")


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


# The refused attempt the pages show and their previews review.
REFUSED_ATTEMPT = UUID(int=6)
# The counts a refused large change recorded.
REFUSED = (
    DropCount("family", 1090, 1084, 25, False),
    DropCount("email_eligible_families", 1084, 612, 25, True),
)


def health(
    now,
    *,
    troubled,
    backup_at=None,
    waiting=False,
    debug=False,
    switch=False,
    refused=False,
    accepted=False,
    mode="production",
):
    """A deployment with several problems, or a healthy one.

    ``debug``: the web portal reports debug logging in effect; ``switch``:
    the debug-off switch is set (ADM-13 PR 6b). ``refused``: a large change
    was refused, and ``accepted``: it was accepted once (ADM-13 PR 5b).
    ``mode``: the global mode.
    """
    refused = refused or troubled
    # A refusal is always an open incident too, so its tile is an Error tile.
    rows = [
        _row(now, "web", debug_logging=debug),
        _row(now, "web", debug_logging=debug),
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
        {"source_destructive_change": now - timedelta(hours=3)} if refused else {}
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
        mode=mode,
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
        daily_sent=1612 if troubled else 240,
        daily_limit=1600,
        daily_resume_at=now + timedelta(hours=5, minutes=10) if troubled else None,
        incidents=incidents,
        refresh=refresh,
        refused_at=now - timedelta(hours=3) if refused else None,
        refused_counts=REFUSED if refused else (),
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
        if troubled or waiting
        else None,
        # Off-site copies are set up, except on the troubled page, where
        # their absence in Production is a warning (decision 5).
        offsite=None
        if troubled
        else SimpleNamespace(
            kind="succeeded",
            message="The last copy reached Google Drive.",
            at=now - timedelta(hours=12),
            last_copy_at=now - timedelta(hours=12),
        ),
        debug_switch=DebugSwitchStatus(
            set_at=now - timedelta(minutes=2), cleared_at=None, cleared_by=None
        )
        if switch
        else None,
        drop_acceptance=DropAcceptanceStatus(
            id=UUID(int=5),
            created_at=now - timedelta(minutes=1),
            expires_at=now + timedelta(hours=24),
            live=True,
        )
        if accepted
        else None,
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

    def values(
        troubled,
        backup_at=None,
        waiting=False,
        debug=False,
        switch=False,
        refused=False,
        accepted=False,
        attempt=REFUSED_ATTEMPT,
        mode="production",
        **extra,
    ):
        """Template context the view would build.

        The troubled page has page-load tiles to count (``LOADED``); its
        polled fragments are rendered with the same ``loaded`` states, as
        the view renders them from ``?loaded=``.
        """
        model = health(
            now,
            troubled=troubled,
            backup_at=backup_at,
            waiting=waiting,
            debug=debug,
            switch=switch,
            refused=refused,
            accepted=accepted,
            mode=mode,
        )
        loaded = extra.pop("loaded", LOADED if troubled else {})
        return (
            context
            | {
                "admin_chrome": chrome,
                "health": model,
                "dashboard": dashboard(
                    model, findings=page_findings(model), loaded=loaded
                ),
                "source_form_count": 0,
                "debug_actor": "other@example.org" if switch else None,
                "accept_actor": "other@example.org" if accepted else None,
                "refused_attempt": attempt if refused else None,
                "campaign_id": None,
                "pause": {"actor": "admin@example.org", "reason": "Checking a typo"}
                if troubled
                else None,
                "poll_interval": POLL_MILLISECONDS,
                # Content to re-save (#838) on the troubled page; none on
                # the healthy one.
                "resave": list(RESAVE) if troubled else [],
            }
            | extra
        )

    return {
        PAGE: (
            "text/html",
            render_to_string(
                "stewardship/system-health.html",
                values(True, status_url=TROUBLED_STATUS),
            ),
        ),
        STALE: (
            "text/html",
            render_to_string(
                "stewardship/system-health.html",
                values(False, status_url=STALE_STATUS),
            ),
        ),
        STALE_STATUS: (
            "text/html",
            render_to_string(
                "stewardship/system-health-status.html", values(True, loaded={})
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
        BACKUP_PAGE: (
            "text/html",
            render_to_string(
                "stewardship/system-health.html",
                values(False, status_url=BACKUP_STATUS),
            ),
        ),
        # The next poll: another Administrator's request is now waiting.
        BACKUP_STATUS: (
            "text/html",
            render_to_string(
                "stewardship/system-health-status.html", values(False, waiting=True)
            ),
        ),
        BACKUP_STEP_UP: (
            "text/html",
            render_to_string(
                "stewardship/system-health.html",
                values(
                    False,
                    status_url=BACKUP_STATUS,
                    backup_now={"refused": "reauthenticate"},
                ),
            ),
        ),
        BACKUP_BUSY: (
            "text/html",
            render_to_string(
                "stewardship/system-health.html",
                values(False, status_url=BACKUP_STATUS, backup_now={"refused": "busy"}),
            ),
        ),
        BACKUP_PREVIEW: (
            "text/html",
            render_to_string(
                "stewardship/system-health.html",
                values(
                    False,
                    status_url=BACKUP_STATUS,
                    backup_now={
                        "preview": BackupPreview(
                            key=UUID(int=706),
                            backup_at=now - timedelta(hours=13),
                            backup_bytes=48 * 1024 * 1024,
                            offsite=False,
                            held=True,
                        )
                    },
                ),
            ),
        ),
        BACKUP_REQUESTED: (
            "text/html",
            render_to_string(
                "stewardship/system-health.html",
                values(
                    False,
                    status_url=BACKUP_STATUS,
                    waiting=True,
                    backup_now={
                        "requested": SimpleNamespace(created_at=now),
                        "created": True,
                    },
                ),
            ),
        ),
        DEBUG_PAGE: (
            "text/html",
            render_to_string(
                "stewardship/system-health.html",
                values(False, debug=True, status_url=DEBUG_STATUS),
            ),
        ),
        DEBUG_STATUS: (
            "text/html",
            render_to_string(
                "stewardship/system-health-status.html",
                values(False, debug=True, switch=True),
            ),
        ),
        DEBUG_PREVIEW: (
            "text/html",
            render_to_string(
                "stewardship/system-health.html",
                values(
                    False,
                    debug=True,
                    status_url=DEBUG_STATUS,
                    debug_off={
                        "preview": DebugOffPreview(
                            key=UUID(int=530), processes=("Web portal",)
                        )
                    },
                    debug_off_shown=True,
                ),
            ),
        ),
        DEBUG_SET: (
            "text/html",
            render_to_string(
                "stewardship/system-health.html",
                values(
                    False,
                    debug=True,
                    switch=True,
                    status_url=DEBUG_STATUS,
                    debug_off={
                        "requested": SimpleNamespace(set_at=now),
                        "created": True,
                    },
                    debug_off_shown=True,
                ),
            ),
        ),
        DEBUG_STEP_UP: (
            "text/html",
            render_to_string(
                "stewardship/system-health.html",
                values(
                    False,
                    debug=True,
                    status_url=DEBUG_STATUS,
                    debug_off={"refused": "reauthenticate"},
                    debug_off_shown=True,
                ),
            ),
        ),
        ACCEPT_PAGE: (
            "text/html",
            render_to_string(
                "stewardship/system-health.html",
                values(False, refused=True, status_url=ACCEPT_STATUS),
            ),
        ),
        ACCEPT_STATUS: (
            "text/html",
            render_to_string(
                "stewardship/system-health-status.html",
                values(False, refused=True, accepted=True),
            ),
        ),
        ACCEPT_PREVIEW: (
            "text/html",
            render_to_string(
                "stewardship/system-health.html",
                values(
                    False,
                    refused=True,
                    status_url=ACCEPT_STATUS,
                    accept={
                        "preview": AcceptPreview(
                            token="signed-binding",
                            attempt_id=REFUSED_ATTEMPT,
                            refused_at=now - timedelta(hours=3),
                            counts=REFUSED,
                            # Synthetic example Families (ADM-13 PR 5c).
                            examples={
                                "email_eligible_families": (
                                    (1201, "Example, Ann and Bob"),
                                    (1202, "Sample, Carol"),
                                )
                            },
                        )
                    },
                    accept_shown=True,
                ),
            ),
        ),
        ACCEPT_DONE: (
            "text/html",
            render_to_string(
                "stewardship/system-health.html",
                values(
                    False,
                    refused=True,
                    accepted=True,
                    status_url=ACCEPT_STATUS,
                    accept={
                        "requested": SimpleNamespace(),
                        "created": True,
                        "refresh": UUID(int=7),
                    },
                    accept_shown=True,
                ),
            ),
        ),
        ACCEPT_STALE: (
            "text/html",
            render_to_string(
                "stewardship/system-health.html",
                values(False, refused=True, status_url=ACCEPT_STALE_STATUS),
            ),
        ),
        ACCEPT_STALE_STATUS: (
            "text/html",
            render_to_string(
                "stewardship/system-health-status.html",
                values(False, refused=True, attempt=UUID(int=8)),
            ),
        ),
        ACCEPT_LATE: (
            "text/html",
            render_to_string(
                "stewardship/system-health.html",
                values(False, status_url=ACCEPT_LATE_STATUS),
            ),
        ),
        ACCEPT_LATE_STATUS: (
            "text/html",
            render_to_string(
                "stewardship/system-health-status.html",
                values(False, refused=True),
            ),
        ),
        ACCEPT_CHANGED: (
            "text/html",
            render_to_string(
                "stewardship/system-health.html",
                values(
                    False,
                    refused=True,
                    status_url=ACCEPT_STATUS,
                    accept={"refused": "busy"},
                    accept_shown=True,
                ),
            ),
        ),
        ALLOW_PAGE: (
            "text/html",
            render_to_string(
                "stewardship/system-health.html",
                values(False, switch=True, mode="testing", status_url=ALLOW_STATUS),
            ),
        ),
        ALLOW_STATUS: (
            "text/html",
            render_to_string(
                "stewardship/system-health-status.html",
                values(False, switch=True, mode="testing"),
            ),
        ),
        ALLOW_PREVIEW: (
            "text/html",
            render_to_string(
                "stewardship/system-health.html",
                values(
                    False,
                    switch=True,
                    mode="testing",
                    status_url=ALLOW_STATUS,
                    debug_off={
                        "allow": True,
                        "preview": DebugAllowPreview(
                            key=UUID(int=1207), set_at=now - timedelta(minutes=2)
                        ),
                    },
                    debug_off_shown=True,
                ),
            ),
        ),
        ALLOW_CLEARED: (
            "text/html",
            render_to_string(
                "stewardship/system-health.html",
                values(
                    False,
                    mode="testing",
                    status_url=ALLOW_STATUS,
                    debug_off={
                        "allow": True,
                        "allowed": SimpleNamespace(cleared_at=now),
                        "created": True,
                    },
                    debug_off_shown=True,
                ),
            ),
        ),
        # What the troubled page's next poll reads: every problem has ended,
        # except the page-load tile it passes as ?loaded=.
        TROUBLED_STATUS: (
            "text/html",
            render_to_string(
                "stewardship/system-health-status.html", values(False, loaded=LOADED)
            ),
        ),
    }
