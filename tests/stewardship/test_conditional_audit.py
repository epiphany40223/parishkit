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

from parishkit.stewardship.campaigns.restore_review import (
    HeldEmails,
    HeldGroup,
    ReviewState,
)
from parishkit.stewardship.jobs.delivery_reads import (
    ACTION_LABELS,
    clear_blocked,
    offered_actions,
)
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
            "decisions": True,
            "retry_label": "Retry failed delivery",
        },
    )
    assert 'id="retry-unavailable"' in html
    # In the Resolve this email section, after the offered forms.
    section = html[html.index('id="resolve-heading"') :]
    assert section.index("Save evidence note") < section.index("retry-unavailable")
    assert (
        'disabled aria-describedby="retry-unavailable">Retry failed delivery</button>'
        in html
    )


def test_delivery_confirms_wait_for_a_recent_sign_in():
    """Without a recent sign-in each Confirm is drawn disabled, named by the
    sign-in notice; with one it is the confirm form."""
    text = (TEMPLATES / "delivery-control.html").read_text()
    assert 'id="delivery-fresh-reason"' in text
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
    ("name", "reason", "count", "notices"),
    [
        ("report-export.html", "export-locked", 3, 1),
        ("report-exact.html", "export-locked", 2, 1),
        # Only Ministry requests' packet is held: one Ministry's lists and
        # their export were retired (#1157), and the packet moved from the
        # summary page to its own include (#1133).
        ("ministry-packet.html", "ministry-export-locked", 1, 1),
        ("participation.html", "participation-export-locked", 2, 1),
        ("information.html", "information-locked", 4, 1),
        # Save, Save and next, and (#1331) the bulk bar and both kinds of box.
        ("ministry-followup.html", "followup-locked", 6, 1),
    ],
)
def test_locked_report_actions_name_the_notice_that_says_why(
    name, reason, count, notices
):
    """While other campaign work holds them, the buttons name its notice."""
    text = (TEMPLATES / name).read_text()
    assert text.count(f'id="{reason}"') == notices
    # The notice's own id, then one mention in each button that names it.
    assert text.count(reason) == count + notices


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


def unavailable(html, label, reason):
    """Assert ``label``'s every button is disabled and names ``reason``,
    whose element is on the page."""
    buttons = re.findall(rf"<button[^>]*>{re.escape(str(label))}</button>", html)
    assert buttons, label
    for button in buttons:
        assert " disabled" in button, button
        assert re.search(rf'aria-describedby="[^"]*\b{reason}\b', button), button
    assert f'id="{reason}"' in html


def test_testing_cleanup_without_a_recent_sign_in_is_unavailable():
    """Start Testing cleanup is in view, named by the sign-in step (#1331)."""
    text = (TEMPLATES / "go-live-readiness.html").read_text()
    assert 'id="cleanup-fresh-reason"' in text
    assert 'disabled=True aria-describedby="cleanup-fresh-reason"' in text


def test_queue_without_a_weekly_schedule_is_unavailable():
    """No Weekly Admin digest schedule: Queue is named by the notice (#1331)."""
    html = render_to_string(
        "stewardship/weekly-manual.html", {"form": None, "blocker": "no_schedule"}
    )
    unavailable(html, "Queue new manual report", "manual-no-schedule")


def test_held_decisions_on_the_mail_message_page_are_unavailable():
    """While campaign work holds the email, its decisions are named by the
    held notice, in the Resolve section, with no form to send (#1331)."""
    held = [
        dict(action="accept", label=ACTION_LABELS["accept"]),
        dict(action="resend", label=ACTION_LABELS["resend"]),
    ]
    html = render_to_string(
        "stewardship/delivery.html",
        {
            "delivery": {"id": uuid4(), "state": "delivery_unknown", "version": 1},
            "commands": [],
            "held": held,
            "decisions": True,
        },
    )
    section = html[html.index('id="resolve-heading"') :]
    for command in held:
        unavailable(section, command["label"], "delivery-held")
    assert "<form" not in section.split("</section>")[0]


def test_an_unavailable_retry_shows_without_other_commands():
    """With no other command offered, the unavailable retry still fills the
    Resolve section rather than leaving it empty (#1331)."""
    html = render_to_string(
        "stewardship/delivery.html",
        {
            "delivery": {"id": uuid4(), "state": "permanent_failure", "version": 1},
            "commands": [],
            "retry_unavailable": True,
            "decisions": True,
            "retry_label": "Retry failed delivery",
        },
    )
    unavailable(html, "Retry failed delivery", "retry-unavailable")


@pytest.mark.parametrize(
    ("flags", "reason"),
    [
        (
            dict(current=False, archived=False, admitted=False, fresh=False),
            "no_campaign",
        ),
        (dict(current=True, archived=True, admitted=True, fresh=True), "archived"),
        (dict(current=True, archived=False, admitted=False, fresh=False), "busy"),
        (dict(current=True, archived=False, admitted=True, fresh=False), "stale"),
        (dict(current=True, archived=False, admitted=True, fresh=True), None),
    ],
)
def test_a_refusal_clearance_names_its_first_missing_condition(flags, reason):
    """The first condition a clearance lacks names why Clear is unavailable."""
    assert clear_blocked(**flags) == reason


@pytest.mark.parametrize(
    ("reason", "words"),
    [
        ("no_campaign", "there is no current campaign"),
        ("archived", "the current campaign is archived"),
        ("busy", "other campaign work"),
        ("stale", "up-to-date parish data from ParishSoft"),
    ],
)
def test_clear_is_unavailable_with_its_specific_reason(reason, words):
    """Clear stays in view, named by its specific reason (#1331)."""
    html = render_to_string(
        "stewardship/delivery-refusal.html",
        {
            "refusal": {"id": uuid4(), "family_duid": 1, "address": "a@b.org"},
            "can_clear": False,
            "clear_blocked": reason,
            "source": {"snapshot_id": uuid4(), "generation": 1},
        },
    )
    unavailable(html, "Clear verified refusal", "refusal-clear-unavailable")
    assert words in html
    assert "<textarea" not in html


def _review(groups=(), needed=0):
    """A Restore review with ``groups`` found and ``needed`` still to hold."""
    from django.utils import timezone

    now = timezone.now()
    return ReviewState(
        restore_id=uuid4(),
        runtime_version=1,
        backup_at=now,
        restored_at=now,
        mode="production",
        campaign_name="2054",
        campaign_state="active",
        listed=bool(groups),
        groups=tuple(groups),
        in_flight=0,
        needed=needed,
    )


GROUP = HeldGroup(uuid4(), "initial", None, unreviewed=2, reachable=2)


def _restore_page(review, *, fresh=True, refusal="", step=None):
    """The Restore review page as its view renders it."""
    return render_to_string(
        "stewardship/restore-review.html",
        {
            "review": review,
            "step": step or {},
            "release_ready": not refusal,
            "release_refusal": refusal,
            "fresh": fresh,
            "page_url": "/admin/maintenance",
        },
    )


def test_release_while_a_campaign_change_runs_is_unavailable_with_why():
    """A running purge or campaign change, found on GET, holds Release (#1331)."""
    html = _restore_page(_review(), refusal="blocked")
    unavailable(html, "Release the site", "restore-release-reason")
    assert "a campaign change or a purge is still running" in html


def test_restore_decisions_wait_for_a_recent_sign_in():
    """Without a recent sign-in every decision and the release name the one
    step-up notice; with one they are available (#1331)."""
    stale = _restore_page(_review([GROUP]), fresh=False)
    assert stale.count('id="restore-fresh-reason"') == 1
    for label in ("Release the site", "Assume these were sent", "Send these again"):
        unavailable(stale, label, "restore-fresh-reason")
    fresh = _restore_page(_review([GROUP]))
    assert "restore-fresh-reason" not in fresh
    assert "data-step-up-notice" not in fresh
    # A refused step's own step-up takes the id, after the panel's controls.
    refused = _restore_page(
        _review([GROUP]),
        fresh=False,
        step={"refused": "reauthenticate", "panel": "release"},
    )
    assert refused.count('id="restore-fresh-reason"') == 1
    assert refused.index("Release the site") < refused.index('restore-fresh-reason"')


def test_held_email_decisions_wait_for_a_recent_sign_in():
    """The Held emails page names its step-up notice the same way (#1331)."""
    held = HeldEmails(groups=(GROUP,))
    html = render_to_string(
        "stewardship/held-emails.html",
        {"held": held, "step": {}, "fresh": False, "page_url": "/held"},
    )
    assert html.count('id="restore-fresh-reason"') == 1
    for label in ("Assume these were sent", "Send these again"):
        unavailable(html, label, "restore-fresh-reason")
