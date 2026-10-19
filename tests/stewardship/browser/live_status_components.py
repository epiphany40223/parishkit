"""Live-status pages at their real addresses, for their updates and Dismiss.

#519 PR 7a and 7b, #1586: each page updates itself by re-reading itself (or
its passive status), and an integration's Dismiss re-reads the page, so the
page is served where those reads lead. A test answers a read with another of
these pages (a route), so the fresh region differs; the integration's Dismiss
is answered by the fixture server with a real Post/Redirect/Get redirect
(``POSTS``).
"""

from datetime import timedelta
from types import SimpleNamespace
from uuid import UUID

from django.template.loader import render_to_string
from django.urls import reverse

from parishkit.stewardship.accounts.integration_credentials import CredentialSummary
from parishkit.stewardship.accounts.integration_forms import IntegrationForm
from parishkit.stewardship.campaigns.domain import Percentage
from parishkit.stewardship.jobs.send_progress_views import (
    GIVE_UP_MILLISECONDS,
    POLL_MILLISECONDS,
    _announcement,
)
from parishkit.stewardship.jobs.task_wording import phase_words
from parishkit.stewardship.jobs.views import EVENT_SORTING
from parishkit.stewardship.source.data_age import Connection
from parishkit.stewardship.source.refresh_status import FullRefreshStatus
from parishkit.stewardship.web.contracts import PageWindow
from parishkit.stewardship.web.tables import window_table

from .automation_components import canonical
from .family_test_components import family_tests_page
from .send_progress_components import _send

REQUEST, RUNNING = UUID(int=700), UUID(int=703)
# A configuration change still being applied when its page opens, so the
# page watches it from the start (#1568 moved these from the retired Key
# replacement status page).
CHANGE_RUNNING = reverse("admin:configuration_request", args=[RUNNING])
PROGRESS = reverse("admin:family_email_progress")
# The passive status the progress page polls while a send runs.
PROGRESS_STATUS = "/live-progress-status"
FAMILY_TESTS = reverse("admin:campaign_mail_families", args=[UUID(int=702)])
# The same page while a Family test is on its way, so it watches itself
# until a read brings the settled tests.
FAMILY_TESTS_PENDING = "/live-family-tests-pending"
INTEGRATION = reverse("admin:integration_settings", args=["parishsoft"])
DISMISS = reverse("admin:dismiss_credential_result", args=["parishsoft"])
# A ParishSoft refresh's task page and the passive status it polls (#869).
TASK_RUN = UUID(int=869)
TASK_PAGE = reverse("admin:background_task_page", args=[TASK_RUN])
TASK_STATUS = reverse("admin:background_task_status", args=[TASK_RUN])
# The finished run's page with its history sorted by Action.
TASK_FINISHED_BY_ACTION = "/live-task-finished-by-action"
# A report export and a latest-data export, and a configuration change's
# status, which update themselves (#519 PR 7b, #1586). The two exports share
# one address form (the view draws whichever the job is).
EXPORT = reverse("admin:report_export", args=[UUID(int=720)])
EXACT = reverse("admin:report_export", args=[UUID(int=721)])
CHANGE = reverse("admin:configuration_request", args=[UUID(int=722)])
# A ready file whose download window ends EXPORT_WINDOW_LEFT after the page's
# server time (#1398), so a fake clock can pass its end.
EXPORT_EXPIRING = "/live-export-expiring"
EXPORT_WINDOW_LEFT = timedelta(minutes=5)
# A ready file's page drawn just after its window's end by the server's clock.
EXPORT_ENDED = "/live-export-ended"
# The same export still being prepared, so its page watches itself until a
# poll brings the ready (EXPORT_EXPIRING) region in.
EXPORT_PENDING = "/live-export-pending"
# Refresh from ParishSoft, which shows the run it requested (#521). The real
# view redirects Refresh now back to the page itself and remembers the run in
# the session; the static fixture server cannot remember, so its redirect
# adds a query that stands in for the session (the same path, so the answer
# is still this page to ui-v1.js).
REFRESH = reverse("admin:source_refresh")
REFRESH_REQUESTED = REFRESH + "?run=requested"
# A bare status region holding a kept element (data-live-keep, #530): its
# page, with a tick box and a button about "a", and the status it polls,
# which moves on to step two and names "b". KEEP_EMPTY draws the kept
# element empty, and its status fills it.
KEEP = "/live-keep"
KEEP_STATUS = "/live-keep/status"
KEEP_EMPTY = "/live-keep-empty"
KEEP_EMPTY_STATUS = "/live-keep-empty/status"
# The fixture server's answers to Dismiss and Refresh now (status, Location,
# body).
POSTS = {
    DISMISS: (303, INTEGRATION + "?dismissed=1", ""),
    REFRESH: (303, REFRESH_REQUESTED + "#refresh-run", ""),
}


def keep_page(step, kept, *, key, status=None):
    """A bare page (or, without ``status``, its polled fragment) whose
    status region says ``step`` and holds a kept element with ``kept``
    contents, naming ``key``."""
    from django.templatetags.static import static

    region = (
        f'<div data-live-status="keep" data-live-interval="2000" data-live-pending'
        f"{f' data-live-url={chr(34)}{status}{chr(34)}' if status else ''}>"
        f"<p data-step>{step}</p>"
        f'<div data-live-keep="box" data-live-keep-key="{key}">{kept}</div></div>'
    )
    if status is None:
        return region
    return (
        '<!doctype html><html lang="en"><head><title>Kept</title>'
        f'<script src="{static("stewardship/live-status-v1.js")}" defer></script>'
        f"</head><body><main><h1>Kept</h1>{region}</main></body></html>"
    )


# What the kept element of KEEP holds: a tick box, and a part about "a" with
# its reserved note and the button a change holds.
KEPT = (
    '<form><label><input type="checkbox" name="tick"> Tick</label>'
    '<div data-live-keep-for="a"><p data-live-stale-note="Changed."></p>'
    '<button type="submit" data-live-stale-holds>Go</button></div></form>'
)


def components(context, admin):
    """Each page as first shown, and the fresh versions the tests swap in."""
    now = context["server_now"]
    page = context | {"admin_chrome": admin}

    def progress(template="family-email-progress", **counts):
        """Family email progress for a running send with these counts, which
        the page watches through PROGRESS_STATUS."""
        send = _send(now, **counts)
        return render_to_string(
            f"stewardship/{template}.html",
            page
            | {
                "status_url": PROGRESS_STATUS,
                "campaign": SimpleNamespace(pk=UUID(int=413)),
                "testing": False,
                "paused": False,
                "send": send,
                "upcoming": False,
                "follow": True,
                "announcement": _announcement(send),
                "poll_interval": POLL_MILLISECONDS,
                "give_up": GIVE_UP_MILLISECONDS,
            },
        )

    def family_tests(items, pending=False):
        """Send to chosen Families with these recent tests, one of them on
        its way while ``pending``."""
        return family_tests_page(
            context,
            admin,
            confirm=False,
            families=[],
            items=items,
            refresh=pending,
            refresh_url=FAMILY_TESTS,
        )

    def integration(summary):
        """ParishSoft's settings with ``summary`` as its key-change status."""
        return render_to_string(
            "stewardship/integration-settings.html",
            page
            | {
                "target": "parishsoft",
                "label": "ParishSoft",
                "summary": summary,
                "credential": None,
                "form": IntegrationForm(
                    "parishsoft",
                    initial={"organization_id": 12345, "base_digest": "a" * 64},
                ),
            },
        )

    def refresh_task(state, steps, *, fragment=False, sort=None):
        """A ParishSoft refresh's task page (or its polled status fragment) in
        ``state``, with one history row per (action, state, phase, current)
        of ``steps``, out of 4,000 records, its history sorted by ``sort``
        (else the default)."""
        template = "background-task-status" if fragment else "background-task"
        return render_to_string(
            f"stewardship/{template}.html", refresh_values(state, steps, sort=sort)
        )

    def export(state, mutable=True, expires_at=None):
        """A participation export's page in ``state``; while ``mutable`` is
        false, other campaign work holds its commands. ``expires_at`` is a
        ready file's window end."""
        job = SimpleNamespace(
            pk=UUID(int=720),
            format="xlsx",
            requester_id=UUID(int=21),
            parameters={"population_scope": "historical"},
            fact_set=SimpleNamespace(source_generation=3, submission_watermark=4),
            created_at=now.isoformat(),
            browser_timezone="America/Los_Angeles",
        )
        return render_to_string(
            "stewardship/report-export.html",
            page
            | {
                "job": job,
                "status": {
                    "state": state,
                    "expires_at": expires_at.isoformat() if expires_at else None,
                },
                "mutable": mutable,
                "retry_key": UUID(int=723),
                "report_url": "/participation",
                "report_name": "Participation",
            },
        )

    def exact(state, export_id=None):
        """A latest-data export's page in ``state``; once its figures are
        ready, ``export_id`` names the file's export."""
        job = SimpleNamespace(
            pk=UUID(int=721),
            format="xlsx",
            population_scope="current",
            submission_watermark=4,
            through_date=now.date(),
            timezone_configuration=SimpleNamespace(timezone="America/New_York"),
            browser_timezone="America/Los_Angeles",
            created_at=now.isoformat(),
        )
        return render_to_string(
            "stewardship/report-exact.html",
            page
            | {
                "job": job,
                "status": {"state": state, "export_id": export_id},
                "source": {"generation": 3, "promoted_at": now.isoformat()},
                "mutable": True,
                "retry_key": UUID(int=724),
                "report_url": "/participation",
            },
        )

    def change(state, request=None):
        """Change status for a Parish settings change ``request`` in ``state``."""
        request = request or UUID(int=722)
        return render_to_string(
            "stewardship/configuration-request.html",
            page
            | {
                "receipt": {"state": state, "request_id": request},
                "admin_chrome": admin
                | {"back": {"label": "Parish settings", "url": "/parish-settings"}},
            },
        )

    def refresh_page(state=None, steps=(), *, minutes=30):
        """Refresh from ParishSoft with data ``minutes`` old, showing the run
        this sign-in requested in ``state`` when there is one, as the view
        draws it from the task's own values."""
        run = (
            refresh_values(state, steps) | {"run_task_id": TASK_RUN} if state else page
        )
        return render_to_string(
            "stewardship/source-refresh.html",
            run
            | {
                "full_refresh": FullRefreshStatus(
                    now,
                    None,
                    False,
                    full_started_at=now - timedelta(minutes=minutes),
                    data_as_of=now - timedelta(minutes=minutes),
                    connection=Connection("working", now),
                ),
                "pending": {"running": False, "waiting": False},
                "request_key": UUID(int=521 + minutes),
                "lateness_minutes": 30,
            },
        )

    def refresh_values(state, steps, *, sort=None):
        """The task page's values for a refresh in ``state`` with ``steps``,
        its history sorted by ``sort`` (else the default)."""

        def progress(phase, current):
            """A progress record as task_page builds it, worded for a refresh."""
            return {
                "phase": phase,
                "current": current,
                "total": 4000,
                "display": Percentage(current, 4000),
            }

        events = [
            {
                "version": version,
                "at": now.isoformat(),
                "action": action,
                "state": step_state,
                "progress": progress(phase, current),
                "phase_text": phase_words("source_refresh", phase),
            }
            for version, (action, step_state, phase, current) in enumerate(steps, 1)
        ]
        phase, current = steps[-1][2:]
        values = page | {
            "is_refresh": True,
            "refresh_label": "Full refresh",
            "phase_text": phase_words("source_refresh", phase),
            "refresh_result": "4,000 records checked, 12 changed."
            if state == "succeeded"
            else None,
            "task": {
                "id": TASK_RUN,
                "name": "ParishSoft data refresh",
                "type": "source_refresh",
                "state": state,
                "active": True,
                "attempt": 1,
                "retry_sequence": 0,
                "created_at": now.isoformat(),
                "updated_at": now.isoformat(),
                "progress": progress(phase, current),
            },
            "work": {"events": events},
            "status_url": TASK_STATUS,
            "history": window_table(
                PageWindow(1, 20),
                events,
                False,
                total=(len(events), False),
                sorting=EVENT_SORTING,
                sort=sort or EVENT_SORTING.default,
            ),
        }
        return values

    # A refresh part-way through its download, then finished: the bar and
    # the history table must both end on the last step.
    running = [
        ("created", "queued", "queued", 0),
        ("progress", "running", "fetching", 1000),
    ]
    finished = [
        *running,
        ("progress", "running", "promoting", 4000),
        ("complete", "succeeded", "promoting", 4000),
    ]

    failed = CredentialSummary(
        "failed", now, "ParishSoft did not accept the new API key.", REQUEST
    )
    tested = [{"duid": 1234, "label": "Sent", "created_at": now - timedelta(minutes=1)}]
    pages = {
        CHANGE_RUNNING: change("staged", RUNNING),
        "/live-running-pending": change("staged", RUNNING),
        "/live-running-applied": change("applied", RUNNING),
        PROGRESS: progress(),
        "/live-progress-later": progress(sent=900, remaining=180),
        PROGRESS_STATUS: progress(
            "family-email-progress-status", sent=900, remaining=180
        ),
        FAMILY_TESTS: family_tests([]),
        FAMILY_TESTS_PENDING: family_tests([], pending=True),
        "/live-family-tests-later": family_tests(tested),
        INTEGRATION: integration(failed),
        INTEGRATION + "?dismissed=1": integration(None),
        TASK_PAGE: refresh_task("running", running),
        TASK_STATUS: refresh_task("running", running, fragment=True),
        "/live-task-finished": refresh_task("succeeded", finished),
        "/live-task-finished-status": refresh_task(
            "succeeded", finished, fragment=True
        ),
        REFRESH: refresh_page(),
        REFRESH_REQUESTED: refresh_page("queued", running[:1]),
        "/live-refresh-finished": refresh_page("succeeded", finished, minutes=1),
        KEEP: keep_page("one", KEPT, key="a", status=KEEP_STATUS),
        KEEP_STATUS: keep_page("two", "", key="b"),
        KEEP_EMPTY: keep_page("one", "", key="", status=KEEP_EMPTY_STATUS),
        KEEP_EMPTY_STATUS: keep_page("two", "<p data-filled>Filled</p>", key=""),
        EXPORT: export("failed", mutable=False),
        "/live-export-ready": export("ready"),
        EXPORT_EXPIRING: export("ready", expires_at=now + EXPORT_WINDOW_LEFT),
        EXPORT_ENDED: export("ready", expires_at=now - timedelta(seconds=1)),
        EXPORT_PENDING: export("queued"),
        EXACT: exact("failed"),
        "/live-exact-ready": exact("succeeded", export_id=UUID(int=725)),
        CHANGE: change("staged"),
        "/live-change-applied": change("applied"),
    }
    # The task page's quiet re-read when its run ends keeps its history's
    # view (#869, #1586): as first shown, and once sorted by Action, each
    # read finds the run finished, sorted as it was asked.
    shown = refresh_values("running", running)["history"]
    action = shown.sort_target("action")
    by_action = refresh_values("running", running, sort=action)["history"]
    pages |= {
        canonical(TASK_PAGE + "?" + shown.heading_query("action")): refresh_task(
            "running", running, sort=action
        ),
        canonical(TASK_PAGE + "?" + shown.current_query): refresh_task(
            "succeeded", finished
        ),
        canonical(TASK_PAGE + "?" + by_action.current_query): refresh_task(
            "succeeded", finished, sort=action
        ),
        # The same, for a test to answer any read of the sorted page with.
        TASK_FINISHED_BY_ACTION: refresh_task("succeeded", finished, sort=action),
    }
    return {path: ("text/html", body) for path, body in pages.items()}
