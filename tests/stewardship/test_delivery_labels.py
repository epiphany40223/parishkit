"""Attempt-history labels never present an Admin record as a provider outcome."""

from parishkit.stewardship.accounts.templatetags.delivery import (
    delivery_event_label,
    delivery_label,
)
from parishkit.stewardship.jobs.delivery_metadata import PURPOSES


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
