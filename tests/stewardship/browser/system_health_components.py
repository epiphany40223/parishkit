"""Render the real System health templates with sample states (ADM-13, #530)."""

from dataclasses import replace
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
    resave_tile,
    source_form_tile,
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
# The Debug logging tile (#1214 slice 5): the same page, whose next poll
# finds the switch set and no service logging in debug form any more.
DEBUG_TILE = "/system-health-debug-tile"
DEBUG_TILE_STATUS = "/system-health-debug-tile/status"
# Turn off debug logging, answered and followed in place (#1317): per mode,
# a page whose web portal reports debug logging and which polls only once a
# minute, so only the answer's own check can bring the tile up to date; its
# status before the switch is set (``/status``) and after (``/status-set``),
# with no service named any more; and the confirmation's answer (``/set``).
DEBUG_LIVE = "/system-health-debug-live-"
# Take a backup now, followed in place (#1317): a page with no request that
# polls every 2 seconds; its status with no request (``/status``), with the
# request running (``/status-running``) and finished (``/status-finished``);
# and the confirmation's answer, the request waiting (``/requested``).
BACKUP_LIVE = "/system-health-backup-live"
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
# A page drawn with a refusal, whose next poll shows it settled (no refusal).
ACCEPT_SETTLED = "/system-health-accept-settled"
ACCEPT_SETTLED_STATUS = "/system-health-accept-settled/status"
# A page drawn with a refusal accepted once, whose next poll shows that
# acceptance lapsed unused: the refusal can no longer be accepted, so the
# section goes and the problem line says why (2026-10-15).
ACCEPT_LAPSED = "/system-health-accept-lapsed"
ACCEPT_LAPSED_STATUS = "/system-health-accept-lapsed/status"
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
# The Mail tile in each state its pk-local check names (slice 3 of #1214):
# a send, an Administrator's pause, the daily limit and a stopped mail
# sender. Each page polls its own status, which brings the same state.
MAIL_STATES = ("sending", "paused", "daily", "stopped")
MAIL = "/system-health-mail-"
LOADED = loaded_states(0, list(RESAVE))
# The page-load and services tiles (#1214 slice 6), each page in one state
# (``TILE_STATES``) that its own poll brings again: Families the form cannot
# open, Pages and emails to re-save and Version and services all Working,
# all Warning (Families listed, an item to re-save, a stopped installer),
# Error where they can be (a blocked email, different versions, a database
# that does not match and a stopped worker), or with the two page-load
# checks unable to run.
TILE_STATES = ("working", "warning", "error", "unchecked")
TILE_PAGES = "/system-health-tiles-"
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
    request=None,
):
    """A deployment with several problems, or a healthy one.

    ``debug``: the web portal reports debug logging in effect; ``switch``:
    the debug-off switch is set (ADM-13 PR 6b). ``refused``: a large change
    was refused, and ``accepted``: it was accepted once (ADM-13 PR 5b), or
    ``"lapsed"``: that acceptance ended unused.
    ``mode``: the global mode. ``request``: the newest Take a backup now
    request's page status; without it, ``waiting`` gives a held request.
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
            status=request or "held",
            created_at=now - timedelta(minutes=12),
            held_at=now - timedelta(minutes=2),
            claimed_at=now - timedelta(minutes=1) if request else None,
            finished_at=now if request == "finished" else None,
            failure_kind=None,
        )
        if troubled or waiting or request
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
            live=accepted is True,
        )
        if accepted
        else None,
    )


def services_state(model, now, state):
    """``model``, a healthy read, with its Version and services tile in
    ``state`` (``TILE_STATES``); its problems are found again."""
    if state not in {"warning", "error"}:
        return model
    rows = [
        _row(now, "web"),
        _row(now, "worker", age=600 if state == "error" else 0),
        _row(now, "worker", "source"),
        _row(now, "scheduler", application_version="1.4.1")
        if state == "error"
        else _row(now, "scheduler"),
        _row(now, "mail-dispatch"),
        _row(now, "mail-dispatch", "mail"),
        _row(now, "config-installer", age=600 if state == "warning" else 0),
    ]
    processes = group_processes(rows, now)
    schema = state != "error"
    return replace(
        model,
        processes=processes,
        versions=tuple(sorted({line.version for line in processes if line.running})),
        schema_current=schema,
        problems=find_problems(
            mode=model.mode,
            processes=processes,
            missing=(),
            schema_current=schema,
            incidents=model.incidents,
            refresh=model.refresh,
            backup_at=model.backup_at,
        ),
    )


# The page-load reads of each TILE_STATES page: source_form_summary's and
# resave_summary's answers.
TILE_READS = {
    "working": (0, []),
    "warning": (3, [RESAVE[0]]),
    "error": (0, list(RESAVE)),
    "unchecked": (None, None),
}


def mail_state(model, now, state):
    """``model``, a healthy read, with its Mail tile in ``state``
    (``MAIL_STATES``); its problems are found again from its processes."""
    rows = {
        "daily": [
            _row(now, "mail-dispatch", sender_state="daily_limit"),
            _row(now, "mail-dispatch", "mail", sender_state="daily_limit"),
        ],
        "stopped": [
            _row(now, "mail-dispatch", age=600),
            _row(now, "mail-dispatch", "mail"),
        ],
    }.get(state)
    values = {
        "sending": {"send_active": True},
        "paused": {"delivery_paused": True, "paused_at": now - timedelta(minutes=30)},
        "daily": {"daily_sent": 1612, "daily_resume_at": now + timedelta(hours=5)},
        "stopped": {},
    }[state]
    if rows:
        kept = [line for line in model.processes if not line.sender]
        processes = tuple(kept) + group_processes(rows, now)
        values |= {
            "processes": processes,
            "problems": find_problems(
                mode=model.mode,
                processes=processes,
                missing=(),
                schema_current=True,
                incidents=model.incidents,
                refresh=model.refresh,
                backup_at=model.backup_at,
            ),
        }
    return replace(model, **values)


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
        mail=None,
        request=None,
        tiles=None,
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
            request=request,
        )
        if mail:
            model = mail_state(model, now, mail)
        if tiles:
            model = services_state(model, now, tiles)
        # The page-load reads, as the view makes them (#774, #838).
        form, resave = (
            TILE_READS[tiles] if tiles else (0, list(RESAVE) if troubled else [])
        )
        loaded = extra.pop(
            "loaded",
            loaded_states(form, resave) if tiles else (LOADED if troubled else {}),
        )
        return (
            context
            | {
                "admin_chrome": chrome,
                "health": model,
                "dashboard": dashboard(
                    model, findings=page_findings(model), loaded=loaded
                ),
                "source_form_count": form,
                "source_form_tile": source_form_tile(form),
                "resave_tile": resave_tile(resave),
                "debug_actor": "other@example.org" if switch else None,
                "accept_actor": "other@example.org" if accepted is True else None,
                "refused_attempt": attempt if refused else None,
                "campaign_id": None,
                "pause": {"actor": "admin@example.org", "reason": "Checking a typo"}
                if troubled
                else None,
                "poll_interval": POLL_MILLISECONDS,
                # Content to re-save (#838) on the troubled page; none on
                # the healthy one.
                "resave": resave,
            }
            | extra
        )

    def page(context):
        """The whole page rendered from ``context``, as a component."""
        return "text/html", render_to_string("stewardship/system-health.html", context)

    def status(context):
        """The polled status fragment rendered from ``context``."""
        return "text/html", render_to_string(
            "stewardship/system-health-status.html", context
        )

    # The Mail tile's pages and the live Turn off debug logging pages.
    extra = {}
    for state in MAIL_STATES:
        extra[MAIL + state] = (
            "text/html",
            render_to_string(
                "stewardship/system-health.html",
                values(False, mail=state, status_url=f"{MAIL}{state}/status"),
            ),
        )
        extra[f"{MAIL}{state}/status"] = (
            "text/html",
            render_to_string(
                "stewardship/system-health-status.html", values(False, mail=state)
            ),
        )
    for mode in ("production", "testing"):
        base = DEBUG_LIVE + mode
        extra[base] = page(
            values(
                False,
                debug=True,
                mode=mode,
                status_url=f"{base}/status",
                poll_interval=60000,
            )
        )
        extra[f"{base}/status"] = status(values(False, debug=True, mode=mode))
        extra[f"{base}/status-set"] = status(values(False, switch=True, mode=mode))
        extra[f"{base}/set"] = page(
            values(
                False,
                switch=True,
                mode=mode,
                status_url=f"{base}/status",
                debug_off={"requested": SimpleNamespace(set_at=now), "created": True},
                debug_off_shown=True,
            )
        )
    extra[BACKUP_LIVE] = page(
        values(False, status_url=f"{BACKUP_LIVE}/status", poll_interval=2000)
    )
    extra[f"{BACKUP_LIVE}/status"] = status(values(False))
    for state in ("running", "finished"):
        extra[f"{BACKUP_LIVE}/status-{state}"] = status(values(False, request=state))
    extra[f"{BACKUP_LIVE}/requested"] = page(
        values(
            False,
            request="waiting",
            status_url=f"{BACKUP_LIVE}/status",
            backup_now={"requested": SimpleNamespace(created_at=now), "created": True},
        )
    )
    # The page-load and services tiles in each state (slice 6), each page
    # polling a status that brings the same state, as the view would render
    # it from the page's ?loaded=.
    for state in TILE_STATES:
        found = values(False, tiles=state)
        query = loaded_query(loaded_states(*TILE_READS[state]))
        url = f"{TILE_PAGES}{state}/status"
        extra[TILE_PAGES + state] = page(
            found
            | {"status_url": f"{url}?{urlencode({'loaded': query})}" if query else url}
        )
        extra[canonical(f"{url}?{urlencode({'loaded': query})}") if query else url] = (
            status(found)
        )
    return extra | {
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
        DEBUG_TILE: (
            "text/html",
            render_to_string(
                "stewardship/system-health.html",
                values(False, debug=True, status_url=DEBUG_TILE_STATUS),
            ),
        ),
        DEBUG_TILE_STATUS: (
            "text/html",
            render_to_string(
                "stewardship/system-health-status.html",
                values(False, switch=True),
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
        ACCEPT_SETTLED: (
            "text/html",
            render_to_string(
                "stewardship/system-health.html",
                values(False, refused=True, status_url=ACCEPT_SETTLED_STATUS),
            ),
        ),
        ACCEPT_SETTLED_STATUS: (
            "text/html",
            render_to_string(
                "stewardship/system-health-status.html",
                values(False),
            ),
        ),
        ACCEPT_LAPSED: (
            "text/html",
            render_to_string(
                "stewardship/system-health.html",
                values(
                    False, refused=True, accepted=True, status_url=ACCEPT_LAPSED_STATUS
                ),
            ),
        ),
        ACCEPT_LAPSED_STATUS: (
            "text/html",
            render_to_string(
                "stewardship/system-health-status.html",
                values(False, refused=True, accepted="lapsed"),
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
