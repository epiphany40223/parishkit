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


def test_delivery_confirms_never_wait_for_a_sign_in():
    """Each reviewed control's Confirm is its form, whatever the sign-in's
    age (#1547): no sign-in notice names it."""
    text = (TEMPLATES / "delivery-control.html").read_text()
    assert "fresh" not in text.replace("refresh", "")
    assert text.count('<input type="hidden" name="action" value="confirm">') == 3


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
        ("report-export.html", "export-locked", 2, 1),
        ("report-exact.html", "export-locked", 2, 1),
        # Only Ministry requests' packet is held: one Ministry's lists and
        # their export were retired (#1157), and the packet moved from the
        # summary page to its own include (#1133).
        ("ministry-packet.html", "ministry-export-locked", 1, 1),
        # The Response dashboard's export button (#1537), and its unavailable
        # stand-in without a chart, which then names both reasons.
        ("response-dashboard.html", "participation-export-locked", 2, 1),
        # Additional information's notice (#1685): its worklist offers no
        # decision while held, and its export pane's button names the notice.
        ("information.html", "information-locked", 0, 1),
        ("information-worklist-export.html", "information-locked", 1, 0),
        # A request page's Save and Save and next (both of its branches).
        ("ministry-followup.html", "followup-locked", 3, 1),
        # The review worklist (#1684) draws no action while the page is held,
        # so its notice is named by nothing.
        ("ministry-requests.html", "followup-locked", 0, 1),
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


@pytest.mark.parametrize("entered", [False, True])
def test_a_held_roster_tick_is_shown_unavailable_with_why(entered):
    """While other campaign work holds follow-up, the request page's Mark
    entered in ParishSoft or Back to review is drawn disabled, named by the
    locked notice; otherwise it is the plain enabled button. (Back to review
    opens its dialog, so it is no submit button and has no
    data-server-disabled; since #1893 it carries the worklist's undo icon.)"""
    label = (
        r'<svg class="action-icon"[^>]*>.*?</svg>\s+Back to review'
        if entered
        else "Mark entered in ParishSoft"
    )
    row = {
        "id": uuid4(),
        "roster_eligible": True,
        "roster_entered": entered,
        "roster_sequence": 1,
    }
    undo = {
        "label": "Back to review",
        "title": "Send Anne — Choir back to review",
        "dialog_title": "Send Anne — Choir back to review?",
        "body": "Entered by hand is undone.",
        "done": "Anne — Choir sent back to review.",
    }
    context = {
        "row": row,
        "can_tick": True,
        "roster_key": uuid4(),
        "roster_undo": undo,
    }
    template = "stewardship/roster-tick.html"
    held = render_to_string(template, context | {"mutable": False})
    assert re.search(
        rf"<button[^>]*disabled(?: data-server-disabled)?"
        rf' aria-describedby="followup-locked"[^>]*>{label}</button>',
        held,
    )
    open_ = render_to_string(template, context | {"mutable": True})
    assert re.search(rf"<button(?![^>]*disabled)[^>]*>{label}</button>", open_)
    assert "followup-locked" not in open_


def test_participation_exports_without_their_data_are_shown_unavailable():
    """With no chart the Response dashboard's export buttons are drawn
    disabled, named by the sentence that says why, and also by the locked
    notice while other campaign work holds exports."""
    text = (TEMPLATES / "response-dashboard.html").read_text()
    reason = "participation-chart-export-unavailable"
    assert text.count(f'id="{reason}"') == 1
    assert (
        '{% export_actions "csv" "xlsx" "pdf" name="participation" '
        "zone_value=query.timezone enabled=False describedby=export_allowed"
        f'|yesno:"{reason},{reason} participation-export-locked" %}}'
    ) in text


def test_family_portal_message_error_is_shown_at_the_field():
    """A refused message comes back in its field, marked, with the reason."""
    html = render_to_string(
        "stewardship/family-portal-maintenance.html",
        {
            "state": {"closed": False},
            "message_limit": 500,
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


def test_testing_cleanup_needs_no_recent_sign_in():
    """A signed cleanup preview offers Start Testing cleanup at once (#1547)."""
    text = (TEMPLATES / "go-live-readiness.html").read_text()
    assert "cleanup-fresh-reason" not in text
    assert '<input type="hidden" name="action" value="cleanup">' in text


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


def test_no_retry_panel_without_other_commands():
    """With no command offered (an archived campaign), the unavailable retry
    is not drawn on its own, so its reason never misnames the blocker; held
    decisions carry the retry with the held notice instead (#1331)."""
    html = render_to_string(
        "stewardship/delivery.html",
        {
            "delivery": {"id": uuid4(), "state": "permanent_failure", "version": 1},
            "commands": [],
            "retry_unavailable": True,
            "decisions": False,
            "retry_label": "Retry failed delivery",
        },
    )
    assert "retry-unavailable" not in html
    assert 'id="resolve-heading"' not in html


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
        ("busy", "the campaign is not accepting changes now"),
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


def _restore_page(review, *, refusal="", step=None):
    """The Restore review page as its view renders it."""
    return render_to_string(
        "stewardship/restore-review.html",
        {
            "review": review,
            "step": step or {},
            "release_ready": not refusal,
            "release_refusal": refusal,
            "page_url": "/admin/maintenance",
        },
    )


def test_release_while_a_campaign_change_runs_is_unavailable_with_why():
    """A running purge or campaign change, found on GET, holds Release (#1331)."""
    html = _restore_page(_review(), refusal="blocked")
    unavailable(html, "Release the site", "restore-release-reason")
    assert "a campaign change or a purge is still running" in html


def test_restore_decisions_need_no_recent_sign_in():
    """Every decision and the release are available whatever the sign-in's age
    (#1547); a step refused because the session ended says so after the
    panel's controls."""
    page = _restore_page(_review([GROUP]))
    assert "restore-fresh-reason" not in page
    for label in ("Assume these were sent", "Send these again"):
        buttons = re.findall(rf"<button[^>]*>{label}</button>", page)
        assert buttons and all(" disabled" not in button for button in buttons)
    refused = _restore_page(
        _review([GROUP]), step={"refused": "signed_out", "panel": "release"}
    )
    assert 'data-restore-refused="signed_out"' in refused
    assert refused.index("Release the site") < refused.index(
        'data-restore-refused="signed_out"'
    )


def test_held_email_decisions_need_no_recent_sign_in():
    """The Held emails page offers its decisions the same way (#1547)."""
    held = HeldEmails(groups=(GROUP,))
    html = render_to_string(
        "stewardship/held-emails.html",
        {"held": held, "step": {}, "page_url": "/held"},
    )
    assert "restore-fresh-reason" not in html
    for label in ("Assume these were sent", "Send these again"):
        buttons = re.findall(rf"<button[^>]*>{label}</button>", html)
        assert buttons and all(" disabled" not in button for button in buttons)
