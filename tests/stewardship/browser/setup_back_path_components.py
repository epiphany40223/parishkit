"""Setup's way back from a failed or discarded parish data load (#1353).

The Loading parish data page (of a load an attempt bound before #2201) after
a load failed for good (setup back to collecting) and after a successful
one, the Check parish data page with a current check and with one of an
earlier connection (#2201), and the ParishSoft connection page while a
current check exists. Each is drawn from the real templates and view
helpers; ``progress`` gives the progress observations, and
``progress_status`` the status fragment an open progress page reads.
"""

from datetime import timedelta
from uuid import uuid4

from django.template.loader import render_to_string

from parishkit.stewardship.accounts import setup_progress_views as progress_views
from parishkit.stewardship.accounts.setup_checks import CheckStatus
from parishkit.stewardship.accounts.setup_credential_views import (
    SetupCredentialForm,
)
from parishkit.stewardship.accounts.setup_views import check_page
from parishkit.stewardship.accounts.setup_wizard import build

from ..test_setup_forms import VALUES
from ..test_setup_wizard import draft

PROGRESS_DONE = "/setup-source-progress-done"
PROGRESS_FAILED = "/setup-source-progress-failed"
# The progress page as a re-read finds it once its load was discarded: its
# step bar shows Check parish data not done and Finish setup unavailable.
PROGRESS_VOIDED = "/setup-source-progress-voided"
SOURCE_CHECKED = "/setup-source-checked"
SOURCE_STALE = "/setup-source-stale"
CREDENTIAL_CHECKED = "/setup-credential-checked"
ORGANIZATION = "St. Example Parish (4321)"
TASK_ID = "00000000-0000-4000-8000-000000001353"
FAILED_ORGANIZATION = "Neighboring Parish 1 (1002)"


def progress(now, outcome):
    """One finished load's progress reading, as ``source_progress`` returns it.

    ``outcome`` is "done", "failed" (setup back to collecting), "voided" (no
    longer bound) or "stale" (bound and succeeded, but no longer valid).
    """
    collections = [
        {
            "key": key,
            "count": 1234,
            "done": True,
            "finished": None,
            "expected": None,
        }
        for key in progress_views.COLLECTIONS
    ]
    return {
        "server_now": now.isoformat(),
        "task_id": TASK_ID,
        "task_state": "failed" if outcome == "failed" else "succeeded",
        "setup_state": "collecting",
        "bound": outcome != "voided",
        "loaded": outcome == "done",
        # A failed load names the organization it was for (#1554).
        "organization_label": FAILED_ORGANIZATION if outcome == "failed" else None,
        "phase": "validating",
        "current": 9000,
        "total": 9000,
        "active": False,
        "collections": collections,
        "started_at": (now - timedelta(minutes=9)).isoformat(),
        "heartbeat_at": (now - timedelta(minutes=1)).isoformat(),
        "idle_at": (now + timedelta(minutes=30)).isoformat(),
        "watchdog_at": (now + timedelta(hours=2)).isoformat(),
        "absolute_at": (now + timedelta(hours=12)).isoformat(),
    }


def wizard(task_state, current="source", *, checked=None):
    """The stepper on ``current`` after a load that ended in ``task_state``.

    The email test, which comes before the step (#1578), was accepted, so
    Finish setup follows once the step is done: ``checked`` (by default,
    whether the load succeeded) is whether the parish data check is current
    (#2201).
    """
    sections = VALUES | {
        "branding": {"bundle_id": str(uuid4())},
        "slack": {"enabled": False, "channel_id": ""},
    }
    return build(
        draft(
            sections,
            source=uuid4() if task_state else None,
            task_state=task_state,
            checked=task_state == "succeeded" if checked is None else checked,
        ),
        current,
        credentials={"parishsoft": True, "google_workspace": True},
        tests=frozenset({"mail_test"}),
    )


def check(state, *, current=True, error=None):
    """One parish data check as the Check parish data page shows it (#2201)."""
    ready = state == "ready"
    return CheckStatus(
        uuid4(),
        state,
        current,
        4321,
        "St. Example Parish" if ready else None,
        812 if ready else None,
        2034 if ready else None,
        error,
    )


def progress_status(now, outcome):
    """The status fragment an open progress page reads for ``outcome``."""
    task_state = "failed" if outcome == "failed" else "succeeded"
    return render_to_string(
        "stewardship/setup-source-progress-status.html",
        progress_views._status_context(progress(now, outcome))
        | {"wizard": wizard(task_state if outcome == "done" else "failed")},
    )


def components(context, admin, now, status_url):
    """The progress, Check parish data and ParishSoft connection pages."""

    def page(template, steps, values):
        """One setup page with the shared Admin chrome and this stepper."""
        return (
            "text/html",
            render_to_string(
                f"stewardship/{template}.html",
                context | {"admin_chrome": admin, "wizard": steps} | values,
            ),
        )

    def progress_page(outcome, task_state):
        """The Loading parish data page for one finished load."""
        return page(
            "setup-source-progress",
            wizard(task_state),
            progress_views._status_context(progress(now, outcome))
            | {"status_url": status_url},
        )

    def source_draft(checked):
        """The Check parish data page's draft, its check current or not."""
        return {
            "status": {"attempt_id": uuid4(), "state": "collecting", "version": 4},
            "sections": {},
            "idle_at": now + timedelta(minutes=30),
            "absolute_at": now + timedelta(hours=12),
            "watchdog_at": None,
            "source_task_id": None,
            "source_task_state": None,
            "source_checked": checked,
        }

    def source_page(checked):
        """The Check parish data page for a check, current or not."""
        steps = wizard(None, checked=checked)
        return page(
            "setup-source",
            steps,
            {
                "draft": source_draft(checked),
                "source_step": steps.step("source"),
            }
            | check_page(check("ready", current=checked), ORGANIZATION),
        )

    credential = page(
        "setup-credential",
        wizard(None, "parishsoft", checked=True),
        {
            "draft": source_draft(True),
            "form": SetupCredentialForm("parishsoft", saved=True),
            "target": "parishsoft",
            "label": "ParishSoft",
            "step_label": "ParishSoft connection",
            "saved": True,
            "current": True,
            "organization": 4321,
            "organization_label": "St. Example Parish (4321)",
        },
    )
    return {
        PROGRESS_DONE: progress_page("done", "succeeded"),
        PROGRESS_FAILED: progress_page("failed", "failed"),
        PROGRESS_VOIDED: progress_page("voided", "failed"),
        SOURCE_CHECKED: source_page(True),
        SOURCE_STALE: source_page(False),
        CREDENTIAL_CHECKED: credential,
    }
