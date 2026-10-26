"""Check parish data and the ParishSoft connection page around it (#2201).

The Check parish data page with a current check and with one of an earlier
connection, and the ParishSoft connection page while a current check exists.
Each is drawn from the real templates and view helpers.
"""

from datetime import timedelta
from uuid import uuid4

from django.template.loader import render_to_string

from parishkit.stewardship.accounts.setup_checks import CheckStatus
from parishkit.stewardship.accounts.setup_credential_views import (
    SetupCredentialForm,
)
from parishkit.stewardship.accounts.setup_views import check_page
from parishkit.stewardship.accounts.setup_wizard import build

from ..test_setup_forms import VALUES
from ..test_setup_wizard import draft

SOURCE_CHECKED = "/setup-source-checked"
SOURCE_STALE = "/setup-source-stale"
CREDENTIAL_CHECKED = "/setup-credential-checked"
ORGANIZATION = "St. Example Parish (4321)"


def wizard(current="source", *, checked=False):
    """The stepper on ``current``, its parish data check current or not (#2201).

    The email test, which comes before the step (#1578), was accepted, so
    Finish setup follows once the step is done.
    """
    sections = VALUES | {
        "branding": {"bundle_id": str(uuid4())},
        "slack": {"enabled": False, "channel_id": ""},
    }
    return build(
        draft(sections, checked=checked),
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


def components(context, admin, now):
    """The Check parish data and ParishSoft connection pages."""

    def page(template, steps, values):
        """One setup page with the shared Admin chrome and this stepper."""
        return (
            "text/html",
            render_to_string(
                f"stewardship/{template}.html",
                context | {"admin_chrome": admin, "wizard": steps} | values,
            ),
        )

    def source_draft(checked):
        """The Check parish data page's draft, its check current or not."""
        return {
            "status": {"attempt_id": uuid4(), "state": "collecting", "version": 4},
            "sections": {},
            "idle_at": now + timedelta(minutes=30),
            "absolute_at": now + timedelta(hours=12),
            "watchdog_at": None,
            "source_checked": checked,
        }

    def source_page(checked):
        """The Check parish data page for a check, current or not."""
        steps = wizard(checked=checked)
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
        wizard("parishsoft", checked=True),
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
        SOURCE_CHECKED: source_page(True),
        SOURCE_STALE: source_page(False),
        CREDENTIAL_CHECKED: credential,
    }
