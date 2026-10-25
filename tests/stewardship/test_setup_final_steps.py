"""The tests and Finish are ordinary wizard steps, not a hub of links."""

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
from .test_setup_wizard import draft

NOW = datetime(2026, 9, 27, 12, tzinfo=UTC)


def wizard(current, *, slack=False, tests=frozenset()):
    """The stepper of an otherwise complete draft on ``current``."""
    sections = VALUES | {
        "branding": {"bundle_id": str(uuid4())},
        "slack": {"enabled": slack, "channel_id": "C0123456789" if slack else ""},
    }
    credentials = {"parishsoft": True, "google_workspace": True, "slack": True}
    return build(
        draft(sections, checked=True),
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


@pytest.mark.parametrize("slack", [False, True])
def test_email_test_sends_until_accepted_then_continues(slack):
    """Before acceptance the primary action sends; afterwards it is Continue,
    to the Slack step, which follows Test email whether Slack is on or not
    (#1578)."""
    form = SetupMailForm(
        initial={"test_token": "token", "request_key": uuid4(), "slot": "initial"}
    )
    after = reverse("admin:setup_step", args=["slack"])
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
    send = '<button data-mail-send data-nav-slot="next" type="submit" '
    send += 'form="setup-test">'
    assert send in row
    assert "Send test email" in row
    # No hidden Continue: once a test settles, the live status region
    # re-reads this panel in place, which brings the row back with Continue.
    assert "setup-continue" not in row
    assert 'action="#setup-test-panel" id="setup-test" data-in-place' in waiting
    assert "Send another test" not in waiting
    done = page(
        "setup-mail.html",
        "mail_test",
        wizard=wizard("mail_test", slack=slack, tests=frozenset({"mail_test"})),
        tested=True,
        pending=False,
        **common,
    )
    row = actions(done)
    assert f'class="setup-continue" data-nav-slot="next" href="{after}"' in row
    assert "hidden" not in row and "data-mail-send" not in row
    assert "Send another test" in done


def test_slack_test_follows_the_same_pattern():
    """The Slack test is its own step, right after the Slack step (#1578)."""
    form = SetupNotificationForm(
        initial={"test_token": "token", "request_key": uuid4()}
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
    # Continue comes with the panel's in-place re-read once a test settles.
    assert "setup-continue" not in row
    assert f'href="{reverse("admin:setup_step", args=["slack"])}"' in row  # Back


PENDING_LINE = (
    '<p class="help" id="setup-test-pending">A test is on its way. '
    "Another test can be sent once its result is known.</p>"
)


@pytest.mark.parametrize("tested", [False, True])
def test_send_held_by_a_pending_test_names_its_reason(tested):
    """While a test is pending, each Send is unavailable and names the
    pending line after the action row, and the live status problem line it
    may also name has an id (#1332 item 28); a settled page has neither."""
    form = SetupNotificationForm(
        initial={"test_token": "token", "request_key": uuid4()}
    )
    common = {
        "wizard": wizard("slack_test", slack=True, tests=frozenset({"mail_test"})),
        "form": form,
        "items": [],
        "channel_id": "C0123456789",
        "tested": tested,
    }
    html = page("setup-notification.html", "slack_test", pending=True, **common)
    assert 'data-live-problem-id="setup-test-problem"' in html
    (send,) = re.findall(r"<button [^>]*data-mail-send[^>]*>", html)
    assert " disabled" in send
    assert 'aria-describedby="setup-test-pending"' in send
    assert "data-live-problem-named" in send
    # The line follows the action row, so it never moves the buttons.
    assert PENDING_LINE in html.split(actions(html), 1)[1]
    settled = page("setup-notification.html", "slack_test", pending=False, **common)
    assert "setup-test-pending" not in settled
    assert "data-live-problem-named" not in settled


def test_finish_checks_readiness_as_its_primary_action():
    """Finish ends the sequence with its own submit, not a Continue link."""
    html = page(
        "setup-confirmation.html",
        "finish",
        form=SetupConfirmationForm(initial={"preview_token": "token"}),
        candidate_digest="a" * 64,
    )
    row = actions(html)
    assert (
        '<button type="submit" data-nav-slot="next">'
        "Check readiness and finish setup</button>" in row
    )
    assert "setup-continue" not in row
    # No Review page comes before it (#2145): Back is Check parish data.
    source = reverse("admin:setup_source")
    assert f'class="setup-back" data-nav-slot="back" href="{source}"' in row


def test_only_an_accepted_test_of_the_current_settings_counts():
    """A success with earlier settings or a pending test offers no Continue."""
    assert accepted([{"current": True, "state": "accepted"}])
    assert not accepted([{"current": False, "state": "accepted"}])
    assert not accepted([{"current": True, "state": "queued"}])
    assert not accepted([])


@pytest.mark.parametrize(
    ("template", "fix", "links"),
    [
        (
            "setup-mail.html",
            ("google_workspace", ("mail", "testing", "google_workspace")),
            {
                "Outgoing email settings": "/admin/setup/mail",
                "Testing recipient": "/admin/setup/testing",
                "Google Workspace connection": (
                    "/admin/setup/credentials/google_workspace"
                ),
            },
        ),
        (
            "setup-notification.html",
            ("slack", ("slack",)),
            {"Slack notifications (optional)": "/admin/setup/slack"},
        ),
    ],
)
def test_a_test_that_was_not_sent_links_the_steps_that_fix_it(template, fix, links):
    """A failed test says so in plain language, repeats the provider's
    rejected-key advice and links each step that fixes it, after the action
    row so the notice never moves Back or Send (#1476)."""
    from parishkit.stewardship.accounts.integration_credentials import FAILED
    from parishkit.stewardship.accounts.setup_mail_views import failure

    items = [{"current": True, "state": "not_sent"}]
    slack = template == "setup-notification.html"
    form = (SetupNotificationForm if slack else SetupMailForm)(
        initial={"test_token": "token", "request_key": uuid4()}
    )
    html = page(
        template,
        "slack_test" if slack else "mail_test",
        wizard=wizard("slack_test" if slack else "mail_test", slack=slack),
        form=form,
        items=[],
        tested=False,
        pending=False,
        failure=failure(items, *fix),
        channel_id="C0123456789",
        testing_recipient="t@example.org",
    )
    (notice,) = re.findall(
        r"<div class=\"notice notice-error\" data-setup-test-failed>.*?</div>",
        html,
        re.S,
    )
    assert "The latest test was not sent." in notice
    assert str(FAILED[fix[0]]) in notice
    for label, url in links.items():
        assert f'<a href="{url}">Go to “{label}”</a>' in notice
    assert html.index(actions(html)) < html.index("data-setup-test-failed")


def test_only_the_newest_test_of_these_settings_not_sent_needs_a_fix():
    """A failure with earlier settings, or a newer test, drops the notice."""
    from parishkit.stewardship.accounts.setup_mail_views import FIX, failure

    failed = {"current": True, "state": "not_sent"}
    assert failure([failed], *FIX)["steps"][0].key == "mail"
    assert failure([{"current": True, "state": "queued"}, failed], *FIX) is None
    assert failure([{"current": False, "state": "not_sent"}], *FIX) is None
    assert failure([{"current": True, "state": "delivery_unknown"}], *FIX) is None
    assert failure([], *FIX) is None


def turn_off_forms(html):
    """Each Turn off Slack notifications form on the page (#1499)."""
    return re.findall(r"<form [^>]*data-setup-slack-off>.*?</form>", html, re.S)


def test_test_slack_and_its_failed_notice_offer_turning_slack_off():
    """Test Slack says Slack is optional and offers Turn off Slack
    notifications above its test form; a test that was not sent first says
    to use a real bot token or turn Slack off, with the button beside it.
    Each posts the Slack step's own form with Slack off (#1499)."""
    from parishkit.stewardship.accounts.setup_mail_views import failure
    from parishkit.stewardship.accounts.setup_notification_views import FIX

    form = SetupNotificationForm(
        initial={"test_token": "token", "request_key": uuid4()}
    )
    common = {
        "wizard": wizard("slack_test", slack=True),
        "form": form,
        "items": [],
        "tested": False,
        "pending": False,
        "channel_id": "C0123456789",
    }
    html = page("setup-notification.html", "slack_test", **common)
    (top,) = turn_off_forms(html)
    assert html.index(top) < html.index('id="setup-test"')
    assert "Slack is optional." in top
    failed = page(
        "setup-notification.html",
        "slack_test",
        **common | {"failure": failure([{"current": True, "state": "not_sent"}], *FIX)},
    )
    first, notice = turn_off_forms(failed)
    assert first == top
    advice = failed.split("data-setup-test-failed>", 1)[1]
    assert advice.index("Use a real Slack bot token") < advice.index(notice)
    assert advice.index(notice) < advice.index("A common cause:")
    for each in (top, notice):
        assert 'action="/admin/setup/slack"' in each
        assert '<input type="hidden" name="version" value="3">' in each
        assert '<input type="hidden" name="channel_id" value="">' in each
        assert 'name="enabled"' not in each
        assert "Turn off Slack notifications" in each


def test_the_email_test_has_no_slack_turn_off():
    """Only the Slack pages offer turning Slack off."""
    from parishkit.stewardship.accounts.setup_mail_views import FIX, failure

    form = SetupMailForm(initial={"test_token": "token", "request_key": uuid4()})
    html = page(
        "setup-mail.html",
        "mail_test",
        form=form,
        items=[],
        tested=False,
        pending=False,
        failure=failure([{"current": True, "state": "not_sent"}], *FIX),
        testing_recipient="t@example.org",
    )
    assert "data-setup-test-failed" in html and not turn_off_forms(html)


def test_test_email_says_why_an_earlier_accepted_test_is_sent_again():
    """After its own inputs change, Test email says why its test is sent
    once more, drawn with the page outside the in-place panel, and only while
    the only accepted test is of earlier settings (#1499, #1578)."""
    from parishkit.stewardship.accounts.setup_mail_views import retest

    earlier = {"current": False, "state": "accepted"}
    assert retest([earlier])
    assert retest([{"current": True, "state": "queued"}, earlier])
    assert not retest([{"current": True, "state": "accepted"}, earlier])
    assert not retest([{"current": False, "state": "not_sent"}])
    assert not retest([])
    form = SetupMailForm(initial={"test_token": "token", "request_key": uuid4()})
    common = {
        "form": form,
        "items": [],
        "tested": False,
        "pending": False,
        "testing_recipient": "t@example.org",
    }
    html = page("setup-mail.html", "mail_test", **common | {"retest": True})
    line = html.split("data-setup-retest>", 1)[1].split("</p>", 1)[0]
    assert "The email settings or key changed" in line
    assert "Slack" not in line
    assert html.index("data-setup-retest") < html.index('id="setup-test-panel"')
    assert "data-setup-retest" not in page("setup-mail.html", "mail_test", **common)


def test_turning_slack_off_says_what_it_discards_and_retests():
    """The button's text and the Slack step's help both say that turning
    Slack off discards the token entered for this setup (#1499); it no
    longer asks for the email test again, which it does not change (#1578)."""
    from parishkit.stewardship.accounts.setup_forms import SetupSlackForm

    form = SetupNotificationForm(
        initial={"test_token": "token", "request_key": uuid4()}
    )
    html = page(
        "setup-notification.html",
        "slack_test",
        wizard=wizard("slack_test", slack=True),
        form=form,
        items=[],
        tested=False,
        pending=False,
        channel_id="C0123456789",
    )
    (top,) = turn_off_forms(html)
    assert "discards the Slack token entered for this setup" in top
    assert "must be sent again" not in top
    assert "entered here" not in top
    help_text = str(SetupSlackForm().fields["enabled"].help_text)
    assert "Turning Slack off discards any Slack token entered" in help_text


@pytest.mark.parametrize(
    ("tests", "expected"), [(frozenset(), "mail_test"), ({"mail_test"}, "finish")]
)
@pytest.mark.parametrize("method", ["get", "head"])
def test_test_slack_while_slack_is_off_opens_the_next_step_needing_work(
    monkeypatch, method, tests, expected
):
    """Back or an old tab on Test Slack after Slack was turned off opens the
    step Continue setup opens (#1505), without reading the test's inputs:
    Test email while it has no accepted test, else the next one not done."""
    from django.test import RequestFactory

    from parishkit.stewardship.accounts import setup_notification_views as views

    sections = VALUES | {
        "branding": {"bundle_id": str(uuid4())},
        "slack": {"enabled": False, "channel_id": ""},
    }
    setup_draft = draft(sections, checked=True)
    credentials = {"parishsoft": True, "google_workspace": True}
    monkeypatch.setattr(views, "runtime", object)
    monkeypatch.setattr(views, "view_draft", lambda request, service: setup_draft)
    monkeypatch.setattr(
        views,
        "wizard_for",
        lambda found: build(found, credentials=credentials, tests=tests),
    )
    monkeypatch.setattr(views, "_checked", lambda request, service, response: response)
    monkeypatch.setattr(views, "prepare_test", pytest.fail)
    request = getattr(RequestFactory(), method)("/admin/setup/slack-test")
    response = views.setup_notification(request)
    assert response.status_code == 302
    assert response["Location"] == build(setup_draft).step(expected).href


def test_a_slack_test_post_while_slack_is_off_is_still_refused(monkeypatch):
    """Only a GET or HEAD is redirected; a POST still reaches the refusal, so
    no Slack test is queued while Slack is off (#1505)."""
    from contextlib import nullcontext

    from django.test import RequestFactory

    from parishkit.stewardship.accounts import setup_notification_views as views
    from parishkit.stewardship.accounts import setup_tests

    setup_draft = draft({"slack": {"enabled": False, "channel_id": ""}})
    monkeypatch.setattr(views, "runtime", object)
    monkeypatch.setattr(views, "view_draft", pytest.fail)
    # The real admission refuses the test while Slack is off.
    monkeypatch.setattr(setup_tests, "work_transaction", nullcontext)
    monkeypatch.setattr(setup_tests, "view_draft", lambda request, service: setup_draft)
    monkeypatch.setattr(views, "request_notification", pytest.fail)
    response = views.setup_notification(
        RequestFactory().post("/admin/setup/slack-test", {})
    )
    assert response.status_code == 400


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ("queued", [("active", "In progress"), ("waiting", None), ("waiting", None)]),
        ("submitting", [("done", None), ("active", "In progress"), ("waiting", None)]),
        ("accepted", [("done", None), ("done", None), ("done", "Delivered")]),
        ("not_sent", [("done", None), ("done", None), ("failed", "Failed")]),
        (
            "delivery_unknown",
            [("done", None), ("done", None), ("uncertain", "Not sure it arrived")],
        ),
        (
            "cancelled",
            [("failed", "Not sent (cancelled)"), ("waiting", None), ("waiting", None)],
        ),
    ],
)
def test_the_newest_test_is_drawn_with_the_shared_stage_boxes(state, expected):
    """Test email and Test Slack draw their newest test as the shared stage
    boxes, Queued, Sending and Result, the box a settled test ended on in
    the list's own words; earlier tests stay listed below (#1578)."""
    from parishkit.stewardship.accounts.setup_mail_views import LABELS, progress

    def row(identity, value):
        """One Recent tests row, as ``setup_mail_views._items`` gives it."""
        return {
            "id": identity,
            "state": value,
            "label": str(LABELS[value]),
            "created_at": NOW.isoformat(),
            "current": True,
        }

    boxes = progress([row("new", state), row("old", "accepted")])
    assert [box["key"] for box in boxes] == ["queued", "sending", "result"]
    assert [box["label"] for box in boxes] == ["Queued", "Sending", "Result"]
    for box, (box_state, words) in zip(boxes, expected, strict=True):
        assert box["state"] == box_state
        if words:
            assert box["status"] == words
    assert progress([]) is None
    html = render_to_string(
        "stewardship/setup-test-status.html",
        {"items": [row("new", state), row("old", "accepted")], "empty": "None yet."},
    )
    (boxes_html,) = re.findall(r'<ol class="stage-boxes".*?</ol>', html, re.S)
    assert 'aria-label="Latest test"' in boxes_html
    assert boxes_html.count('data-test-stage="') == 3
    # The newest test's line comes first, the earlier ones after the boxes.
    assert html.index('data-mail-id="new"') < html.index("stage-boxes")
    assert html.index("stage-boxes") < html.index("Earlier tests")
    assert html.index("Earlier tests") < html.index('data-mail-id="old"')
    empty = render_to_string(
        "stewardship/setup-test-status.html", {"items": [], "empty": "None yet."}
    )
    assert "stage-boxes" not in empty and "None yet." in empty
    single = render_to_string(
        "stewardship/setup-test-status.html",
        {"items": [row("new", state)], "empty": "None yet."},
    )
    assert "Earlier tests" not in single and "<ul>" not in single


def test_a_test_sent_with_earlier_settings_draws_no_result():
    """A stale newest test says nothing about the current settings, so its
    boxes are all Not started rather than a green Delivered that reads as
    current (#1578)."""
    from parishkit.stewardship.accounts.setup_mail_views import LABELS, progress

    stale = {
        "id": "stale",
        "state": "accepted",
        "label": str(LABELS["accepted"]),
        "created_at": NOW.isoformat(),
        "current": False,
    }
    boxes = progress([stale])
    assert [box["state"] for box in boxes] == ["waiting"] * 3
    assert [box["status"] for box in boxes] == ["Not started"] * 3
    html = render_to_string(
        "stewardship/setup-test-status.html", {"items": [stale], "empty": "None yet."}
    )
    assert "Sent with earlier settings" in html and "Delivered" in html
    assert "stage-done" not in html
