"""Server-known prerequisites shown unavailable with why (#563, the audit's rest).

Each page here renders an action the server would refuse as a disabled
button named by its visible reason (``aria-describedby``), instead of
leaving it out or enabled. The browser behavior is in
browser/test_conditional_audit.py.
"""

import re
from pathlib import Path
from uuid import uuid4

import pytest
from django.template import Context, Template
from django.template.loader import render_to_string

from parishkit.stewardship.jobs.delivery_reads import ACTION_LABELS, offered_actions
from parishkit.stewardship.jobs.delivery_views import RETRY_ACTIONS

TEMPLATES = (
    Path(__file__).parents[2]
    / "src/parishkit/stewardship/accounts/templates/stewardship"
)


def test_an_empty_aria_value_is_left_out():
    """``aria-describedby=reason`` names a reason only while there is one."""
    template = Template(
        "{% load stewardship %}"
        '{% button type="submit" enabled=ok aria-describedby=ok|yesno:",why" %}'
        "Go{% endbutton %}"
    )
    assert "aria-describedby" not in template.render(Context({"ok": True}))
    refused = template.render(Context({"ok": False}))
    assert 'disabled data-server-disabled aria-describedby="why"' in refused


@pytest.mark.parametrize(
    "state", ["delivery_unknown", "permanent_failure", "pending", "retry_wait"]
)
def test_the_unavailable_retry_is_the_one_the_page_would_offer(state):
    """RETRY_ACTIONS names the retry offered_actions gives each state."""
    offered = offered_actions(
        state, "failed", campaign_state="active", can_resolve=True, can_retry=True
    )
    assert RETRY_ACTIONS[state] in offered
    assert RETRY_ACTIONS[state] in ACTION_LABELS


def test_the_mail_message_page_shows_retry_unavailable_with_why():
    """The retry stays in view, disabled, named by the notice that says why."""
    html = render_to_string(
        "stewardship/delivery.html",
        {
            "delivery": {"id": uuid4(), "state": "permanent_failure", "version": 1},
            "commands": [{"action": "note", "label": "Save evidence note", "id": 1}],
            "retry_unavailable": True,
            "retry_label": "Retry failed delivery",
        },
    )
    assert 'id="retry-unavailable"' in html
    assert (
        'disabled aria-describedby="retry-unavailable">Retry failed delivery</button>'
        in html
    )


def test_delivery_confirms_wait_for_a_recent_sign_in():
    """Without a recent sign-in each Confirm is drawn disabled, named by the
    sign-in notice; with one it is the confirm form."""
    text = (TEMPLATES / "delivery-control.html").read_text()
    assert '<p id="delivery-fresh-reason">' in text
    assert "control_token and fresh" not in text
    assert text.count('aria-describedby="delivery-fresh-reason"') == 3


def test_the_weekly_report_in_progress_is_shown_unavailable():
    """A digest still in progress, found on GET, shows Queue unavailable."""
    html = render_to_string(
        "stewardship/weekly-manual.html", {"form": None, "blocker": "in_progress"}
    )
    assert 'id="manual-unavailable"' in html
    assert re.search(
        r'<button[^>]*disabled aria-describedby="manual-unavailable">'
        r"Queue new manual report</button>",
        html,
    )


@pytest.mark.parametrize(
    ("name", "reason", "count"),
    [
        ("report-export.html", "export-locked", 3),
        ("report-exact.html", "export-locked", 2),
        ("ministry-report.html", "ministry-export-locked", 2),
        ("participation.html", "participation-export-locked", 2),
        ("information.html", "information-locked", 4),
        ("ministry-followup.html", "followup-locked", 3),
    ],
)
def test_locked_report_actions_name_the_notice_that_says_why(name, reason, count):
    """While other campaign work holds them, the buttons name its notice."""
    text = (TEMPLATES / name).read_text()
    assert text.count(f'id="{reason}"') == 1
    # The notice's own id, then one mention in each button that names it.
    assert text.count(reason) == count + 1


def test_family_portal_message_error_is_shown_at_the_field():
    """A refused message comes back in its field, marked, with the reason."""
    html = render_to_string(
        "stewardship/family-portal-maintenance.html",
        {
            "state": {"closed": False},
            "message_limit": 500,
            "fresh": True,
            "message": "Write to a@b.org",
            "message_error": "The maintenance message cannot contain an email address.",
        },
    )
    (field,) = re.findall(r"<textarea[^>]*>", html)
    assert 'aria-invalid="true"' in field
    assert "maintenance-message-error" in field
    assert 'data-pattern="[^@]*"' in field
    assert '<ul class="errorlist" id="maintenance-message-error">' in html
    assert "Write to a@b.org</textarea>" in html
