"""Attempt-history labels never present an Admin record as a provider outcome."""

from pathlib import Path

import parishkit.stewardship.accounts as accounts
from parishkit.stewardship.accounts.templatetags.delivery import (
    delivery_event_label,
    delivery_label,
    delivery_task_label,
)
from parishkit.stewardship.jobs.delivery_metadata import OUTCOMES, PURPOSES, STATES
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
