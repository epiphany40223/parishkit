"""Admin messages link the pages they name (#521, navigation rule 6).

A message that tells the reader to use another page links that page when the
viewer may open it. Each test renders one page's template with just enough
context to show the message, and checks the message's own words around
the link, since the sidebar links the same pages. A link that names a page
the menu can grey out uses menu_link, so it is greyed the same way.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.template.loader import render_to_string
from django.urls import reverse

from parishkit.stewardship.accounts import admin_navigation as navigation

NOW = datetime(2054, 10, 1, tzinfo=UTC)
OUTGOING = f'<a href="{reverse("admin:deliveries")}">Sent mail</a>'


def _production_progress(complete):
    """Render Production activation with initial mail being prepared or done.

    Through the view's own context (``confirmation_views._context``), so the
    page's state and stage boxes are the ones it would draw.
    """
    from parishkit.stewardship.accounts.confirmation_views import _context

    now = datetime(2054, 10, 1, tzinfo=UTC)
    shown = {
        "campaign": SimpleNamespace(
            state="active",
            delivery_paused=False,
            active_configuration=SimpleNamespace(name="Annual", starts_at=now),
        ),
        "receipt": SimpleNamespace(created_at=now),
        "demand": SimpleNamespace(
            items_completed=3, phase="plan", groups_completed=1, failure_code=""
        ),
        "task": SimpleNamespace(state="running", updated_at=now),
        "complete": complete,
        "control": None,
        "outcomes": [],
        "withdrawal_available": False,
    }
    return render_to_string(
        "stewardship/production-progress.html",
        {"admin_chrome": None} | _context(shown),
    )


@pytest.mark.parametrize("complete", [False, True])
def test_production_activation_links_outgoing_mail(complete):
    """Once preparation is done, its "follow delivery" line links Sent mail.

    The Sending box never claims delivery, so the line says where it shows.
    """
    html = _production_progress(complete)
    link = f'<a href="{reverse("admin:deliveries")}">Sent mail</a>'
    assert html.count(link) == (1 if complete else 0)
    if complete:
        assert f"follow delivery on {link}." in html
    assert "See Sent mail" not in html
    assert "see Sent mail" not in html


def _greyed(name):
    """Chrome whose menu greys out ``name``, for the menu_link tag.

    The sign-in times are what a full page's session banner needs.
    """
    item = SimpleNamespace(url=None, reason=navigation.ARCHIVED)
    times = dict.fromkeys(("server_now", "idle_deadline", "absolute_deadline"), NOW)
    return {"entries": {name: item}} | times


def _status(**receipt):
    """Change status's live region for a settled change, with ``receipt``."""
    chrome = receipt.pop("chrome", None)
    receipt = {"state": "failed", "request_id": uuid4(), "refusal": ""} | receipt
    context = {"receipt": SimpleNamespace(**{"failure_code": ""} | receipt)}
    if chrome is not None:
        context["admin_chrome"] = chrome
    return render_to_string("stewardship/configuration-request-status.html", context)


def test_a_refused_end_date_links_campaign_settings():
    """The refusal links Campaign settings, greyed while the menu greys it."""
    html = _status(refusal="Refused.")
    link = f'<a href="{reverse("admin:campaign_settings")}">Campaign settings</a>'
    assert f"review it again on {link}.</p>" in html
    html = _status(refusal="Refused.", chrome=_greyed("campaign_settings"))
    assert link not in html
    assert 'aria-disabled="true"' in html
    assert str(navigation.ARCHIVED) in html
    # A viewer whose menu does not offer Campaign settings reads its name.
    html = _status(
        refusal="Refused.", chrome=_greyed("campaign_settings") | {"entries": {}}
    )
    assert "review it again on Campaign settings.</p>" in html


def test_a_change_failing_validation_links_system_logs():
    """The validation failure's "check System logs" links System logs."""
    html = _status(failure_code="invalid_candidate")
    assert f'check <a href="{reverse("admin:logs")}">System logs</a>.</p>' in html


def test_the_schedules_link_on_family_email_history_follows_the_menu():
    """Dates and mail schedules is linked, and greyed on an archived campaign."""
    about = "earliest reminder on "
    link = (
        f'<a href="{reverse("admin:schedule_settings")}">Dates and mail schedules</a>'
    )
    html = render_to_string("stewardship/family-email-sends.html", {})
    assert f"{about}{link}. A reminder" in html
    greyed = {"admin_chrome": _greyed("schedule_settings")}
    html = render_to_string("stewardship/family-email-sends.html", greyed)
    assert link not in html
    assert f'{about}<span class="disabled-control">' in html


# The counts Pause and resume mail shows for overdue Family email and digests.
FAMILY = ("selected", "coalesced", "skipped", "blocked", "deferred", "unmaterialized")
DIGEST = ("selected", "slots", "unmaterialized", "prepared", "blocked")


def _counts(names, **counts):
    """Recovery counts: zero for each of ``names`` unless ``counts`` sets it."""
    return dict.fromkeys(names, 0) | counts


def _choices(**choices):
    """Pause and resume mail's resolution choices, none available by default.

    A namespace, not a dict: the template's ``resolution.clear`` would call
    a dict's own ``clear`` method.
    """
    empty = {"release": False, "cancel": False, "clear": False, "types": []}
    return SimpleNamespace(**empty | {"uncancellable": []} | choices)


@pytest.mark.parametrize(
    "context, words",
    [
        ({}, f"resolved on the {OUTGOING} page, not here."),
        (
            {"available": True, "family_recovery": _counts(FAMILY, deferred=1)},
            f"invitation on the {OUTGOING} page, or the restore hold",
        ),
        (
            {"available": True, "digest_recovery": _counts(DIGEST, blocked=1)},
            f"deliveries on the {OUTGOING} page, before resuming.",
        ),
        (
            {
                "available": True,
                "resolve_available": True,
                "resolution": _choices(cancel=True, uncancellable=["Receipts"]),
            },
            f"Resolve that email on the {OUTGOING} page first.",
        ),
        (
            {
                "available": True,
                "resolve_available": True,
                "resolution": _choices(types=[1], uncancellable=["Receipts"]),
            },
            f"may not have arrived, on the {OUTGOING} page (Receipts).",
        ),
        (
            {"available": True, "resolve_available": True, "resolution": _choices()},
            f"may not have arrived on the {OUTGOING} page.</li>",
        ),
    ],
)
def test_pause_and_resume_mail_links_outgoing_mail(context, words):
    """Each message that sends the reader to Sent mail links it."""
    campaign = SimpleNamespace(
        active_configuration=None, delivery_paused=True, paused_at=NOW, pause_reason=""
    )
    counts = ("queued", "held", "submitting", "unknown")
    inventory = dict.fromkeys(counts, 0)
    context = {"campaign": campaign, "inventory": inventory, "next_due": {}} | context
    html = render_to_string("stewardship/delivery-control.html", context)
    assert words in html
