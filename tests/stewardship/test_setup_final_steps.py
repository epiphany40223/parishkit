"""Review, the tests and Finish are ordinary wizard steps, not a hub of links."""

import re
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from django.template.loader import render_to_string
from django.urls import reverse

from parishkit.stewardship.accounts.setup_confirmation_views import (
    SetupConfirmationForm,
)
from parishkit.stewardship.accounts.setup_mail_views import SetupMailForm
from parishkit.stewardship.accounts.setup_mail_views import tested as accepted
from parishkit.stewardship.accounts.setup_notification_views import (
    SetupNotificationForm,
)
from parishkit.stewardship.accounts.setup_wizard import build

from .test_setup_forms import VALUES
from .test_setup_wizard import CAMPAIGN, draft

NOW = datetime(2026, 9, 27, 12, tzinfo=UTC)


def wizard(current, *, slack=False, tests=frozenset()):
    """The stepper of a reviewed, otherwise complete draft on ``current``."""
    sections = VALUES | {
        "branding": {"bundle_id": str(uuid4())},
        "campaign": CAMPAIGN,
        "schedules": {"records": []},
        "slack": {"enabled": slack, "channel_id": "C0123456789" if slack else ""},
    }
    credentials = {"parishsoft": True, "google_workspace": True, "slack": True}
    return build(
        draft(sections, source=uuid4(), reviewed={"shares": [], "preview": 3}),
        current,
        credentials=credentials,
        tests=tests,
    )


def page(template, current, **context):
    """Render one final-step page with the shared setup page context."""
    steps = context.pop("wizard", None) or wizard(current)
    setup_draft = {
        "status": {"attempt_id": uuid4(), "state": "collecting", "version": 3},
        "sections": {"slack": {"enabled": False}},
        "idle_at": NOW + timedelta(minutes=30),
        "absolute_at": NOW + timedelta(hours=12),
        "watchdog_at": None,
    }
    return render_to_string(
        f"stewardship/{template}",
        {"draft": setup_draft, "wizard": steps} | context,
    )


def actions(html):
    """The page's one shared Back / primary action row."""
    (row,) = re.findall(r'<div class="setup-actions">.*?</div>', html, re.S)
    return row


def body(html):
    """The page below its heading, i.e. without the stepper's own step links."""
    return html.split("<h1>", 1)[1]


def test_review_has_no_links_to_later_steps_and_continues_to_the_email_test():
    """Only the standard row moves on; the primary action is Continue."""
    html = page(
        "setup-preview.html",
        "preview",
        parish={"name": "Sample Parish", "website": "https://example.invalid/"},
        campaign={"name": "Annual", "modules": ["census"]},
        samples=[],
    )
    content = body(html)
    for route in ("setup_mail", "setup_notification", "setup_confirmation"):
        assert f'href="{reverse(f"admin:{route}")}"' not in content.replace(
            actions(html), ""
        )
    row = actions(html)
    assert f'class="setup-continue" href="{reverse("admin:setup_mail")}"' in row
    assert "Test email" in row and "Back" in row
    # The row comes last, after the samples and the exact configuration.
    assert content.rindex("setup-actions") > content.index("<details>")


@pytest.mark.parametrize("slack", [False, True])
def test_email_test_sends_until_accepted_then_continues(slack):
    """Before acceptance the primary action sends; afterwards it is Continue."""
    form = SetupMailForm(
        initial={"preview_token": "token", "request_key": uuid4(), "slot": "initial"}
    )
    after = reverse("admin:setup_notification" if slack else "admin:setup_confirmation")
    common = {"form": form, "items": [], "testing_recipient": "t@example.org"}
    waiting = page(
        "setup-mail.html",
        "mail_test",
        wizard=wizard("mail_test", slack=slack),
        tested=False,
        pending=False,
        **common,
    )
    row = actions(waiting)
    assert '<button data-mail-send type="submit" form="setup-test">' in row
    assert "Send test email" in row
    # Continue waits, hidden, for the page script to reveal after acceptance.
    assert f'data-mail-continue href="{after}" hidden' in row
    assert 'id="setup-test"' in waiting and "Send another test" not in waiting
    done = page(
        "setup-mail.html",
        "mail_test",
        wizard=wizard("mail_test", slack=slack, tests=frozenset({"mail_test"})),
        tested=True,
        pending=False,
        **common,
    )
    row = actions(done)
    assert f'class="setup-continue" href="{after}"' in row
    assert "hidden" not in row and "data-mail-send" not in row
    assert "Send another test" in done


def test_slack_test_follows_the_same_pattern():
    """The Slack test is its own step: send, then Continue to Finish."""
    form = SetupNotificationForm(
        initial={"preview_token": "token", "request_key": uuid4()}
    )
    html = page(
        "setup-notification.html",
        "slack_test",
        wizard=wizard("slack_test", slack=True, tests=frozenset({"mail_test"})),
        form=form,
        items=[],
        channel_id="C0123456789",
        tested=False,
        pending=True,
    )
    row = actions(html)
    assert "Send Slack test" in row and " disabled" in row
    assert f'href="{reverse("admin:setup_confirmation")}" hidden' in row
    assert f'href="{reverse("admin:setup_mail")}"' in row  # Back


def test_finish_checks_readiness_as_its_primary_action():
    """Finish ends the sequence with its own submit, not a Continue link."""
    html = page(
        "setup-confirmation.html",
        "finish",
        form=SetupConfirmationForm(initial={"preview_token": "token"}),
        candidate_digest="a" * 64,
    )
    row = actions(html)
    assert '<button type="submit">Check readiness and finish setup</button>' in row
    assert "setup-continue" not in row


def test_only_an_accepted_test_of_this_revision_counts():
    """An older revision's success or a pending test does not offer Continue."""
    assert accepted([{"current": True, "state": "accepted"}])
    assert not accepted([{"current": False, "state": "accepted"}])
    assert not accepted([{"current": True, "state": "queued"}])
    assert not accepted([])
