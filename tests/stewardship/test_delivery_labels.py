"""Attempt-history labels never present an Admin record as a provider outcome."""

from pathlib import Path

import parishkit.stewardship.accounts as accounts
from parishkit.stewardship.accounts import (
    campaign_family_test_views,
    setup_mail_views,
    setup_notification_views,
)
from parishkit.stewardship.accounts.templatetags.delivery import (
    delivery_event_label,
    delivery_label,
    delivery_task_label,
)
from parishkit.stewardship.jobs.delivery_metadata import (
    OUTCOMES,
    PURPOSES,
    STATE_LABELS,
    STATES,
)
from parishkit.stewardship.jobs.models import TASK_STATES
from parishkit.stewardship.reports import family_timeline

TEMPLATES = Path(accounts.__file__).parent / "templates" / "stewardship"


def test_admin_unsent_record_is_labelled_by_its_reason():
    """confirm_unsent reuses fail_unaccepted, so its reason names the event."""
    event = {"action": "fail_unaccepted", "reason": "admin_confirmed_unsent"}
    assert str(delivery_event_label(event)) == (
        "Not sent, per provider records; no resend"
    )


def test_provider_outcomes_keep_their_action_label():
    """A provider refusal or any reason without an Admin label shows its action."""
    refused = {"action": "fail_unaccepted", "reason": "smtp_permanent"}
    assert str(delivery_event_label(refused)) == "Not accepted; delivery failed"
    assert str(delivery_event_label({"action": "mark_unknown"})) == (
        "Acceptance unknown"
    )


def test_every_delivery_purpose_has_a_label():
    """No purpose falls back to the generic unknown-value label."""
    fallback = str(delivery_label("no-such-internal-value"))
    for purpose in PURPOSES:
        assert str(delivery_label(purpose)) != fallback, purpose
    assert str(delivery_label("weekly_digest")) == "Weekly Administrator report"


def test_delivery_states_use_the_family_timeline_words():
    """Outgoing mail names each state with the timeline's outcome (#589).

    The Family timeline and the delivery labels read one table, and every
    state's label starts with its plain outcome; "Still sending" adds where
    the email is, so a retry stays visible.
    """
    assert family_timeline.OUTCOMES is OUTCOMES
    for state in STATES[1:]:
        assert str(delivery_label(state)).startswith(str(OUTCOMES[state])), state
    assert [str(delivery_label(state)) for state in STATES] == [
        "All",
        "Not sure it arrived",
        "Failed",
        "Still sending (queued)",
        "Still sending (waiting to retry)",
        "Still sending (handing to the mail service)",
        "Delivered",
        "Not sent (cancelled)",
    ]


def test_task_states_keep_their_own_words():
    """A background task's retry_wait or cancelled is not an email's outcome."""
    assert str(delivery_task_label("retry_wait")) == "Waiting to retry"
    assert str(delivery_task_label("cancelled")) == "Cancelled"
    assert str(delivery_task_label("abandoned")) == "Lease expired"
    for state in TASK_STATES:
        assert str(delivery_task_label(state)) != "Unknown status", state


def test_outgoing_mail_about_panel_defines_the_plain_words():
    """The About panel defines each word the state column and filter use."""
    source = (TEMPLATES / "deliveries.html").read_text()
    for word in ("Delivered", "Not sure it arrived", "Failed", "Still sending"):
        assert f'<dt>{{% translate "{word}" %}}</dt>' in source, word
    assert '<dt>{% translate "Not sent (cancelled)" %}</dt>' in source
    assert "Delivery unknown" not in source


# Pages that count or link to outbox states by name (#678).
STATE_PAGES = (
    "admin-status.html",
    "admin-banners.html",
    "delivery-control.html",
    "family-email-progress.html",
    "family-email-progress-status.html",
    "family-email-sends.html",
    "family-email-sends-table.html",
)
OLD_NAMES = (
    "Delivery unknown",
    "Already submitting",
    '"Uncertain',
    "failed or uncertain",
    "Review uncertain",
    "}} uncertain",
    "not sure to have",
)


def test_state_counts_use_the_plain_words_elsewhere():
    """The status bar, pause pages and Family email progress and sends say
    "Not sure it arrived", the Outgoing mail filter's word, never the old
    names one click away from it (#678)."""
    for name in STATE_PAGES:
        source = (TEMPLATES / name).read_text()
        assert str(OUTCOMES["delivery_unknown"]) in source, name
        for old in OLD_NAMES:
            assert old not in source, (name, old)
    for name in ("admin-banners.html", "delivery-control.html"):
        source = (TEMPLATES / name).read_text()
        assert str(STATE_LABELS["submitting"]) in source, name


def test_test_email_results_use_the_shared_words():
    """Test-email results read like the same result on Outgoing mail (#678).

    A chosen-Family test is an outbox message, so it uses the outbox labels
    as they are; setup's test states map to their nearest outbox words, and
    a Slack test names Slack rather than the mail service.
    """
    assert campaign_family_test_views.MESSAGE_LABELS is STATE_LABELS
    words = {str(label) for label in STATE_LABELS.values()}
    assert {str(label) for label in setup_mail_views.LABELS.values()} <= words
    assert str(setup_mail_views.LABELS["delivery_unknown"]) == "Not sure it arrived"
    assert str(setup_mail_views.LABELS["accepted"]) == "Delivered"
    slack = setup_notification_views.SLACK_LABELS
    assert str(slack["submitting"]) == "Still sending (handing to Slack)"
    assert all("mail" not in str(label) for label in slack.values())
