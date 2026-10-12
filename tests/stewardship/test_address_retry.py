"""Pure rules of a delivered email's follow-up to addresses refused for now (#806).

``settled`` maps one follow-up attempt's provider result (positions in the
envelope just sent) onto the follow-up's stored positions (positions in the
original rendering), and decides whether it waits, finishes or ends. The
database guard (frozen migration 0077) checks the same moves; these tests pin
the Python side, without a database.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from parishkit.stewardship.family_delivery import (
    FamilyDeliveryResult,
    FamilyDeliveryStatus,
    ProviderHealth,
)
from parishkit.stewardship.jobs import address_retry
from parishkit.stewardship.jobs.family_mail_dispatch import (
    LIMIT_RETRY_SECONDS,
    RECIPIENT_RETRY_SECONDS,
)

NOW = datetime(2054, 10, 5, 12, tzinfo=UTC)
Status = FamilyDeliveryStatus


def row(remaining=(1, 3), refused=(), attempt=1, spared=0, days_left=6):
    """A follow-up just submitted to ``remaining`` (rendering positions)."""
    return SimpleNamespace(
        remaining=list(remaining),
        refused=list(refused),
        attempt=attempt,
        spared=spared,
        expires_at=NOW + timedelta(days=days_left),
    )


def result(status, count=2, **kwargs):
    """A provider result for an envelope of ``count`` addresses."""
    return FamilyDeliveryResult(status, count, **kwargs)


def test_accepted_positions_leave_and_only_refused_for_now_stay():
    """Envelope index 1 is rendering position 3: refused for now, it stays;
    position 1 was accepted and is never in a later envelope."""
    values, wait = address_retry.settled(
        row(), result(Status.ACCEPTED, transient=(1,)), NOW
    )
    assert values["remaining"] == [3]
    assert values["refused"] == []
    assert values["outcome"] == {
        "status": "accepted",
        "permanent": [],
        "transient": [3],
    }
    assert values["state"] == "waiting"
    assert values["not_before"] == NOW + wait


def test_everything_accepted_finishes():
    values, _ = address_retry.settled(row(), result(Status.ACCEPTED), NOW)
    assert values["state"] == "finished" and values["remaining"] == []


def test_permanent_refusals_move_to_refused():
    """A position refused for good is set aside and never sent again."""
    values, _ = address_retry.settled(
        row(refused=(0,)), result(Status.ACCEPTED, permanent=(0,)), NOW
    )
    assert values["remaining"] == []
    assert values["refused"] == [0, 1]
    assert values["state"] == "finished"


def test_all_refused_for_now_waits_on_the_throttle_schedule():
    """Every address refused for now: the message's per-address schedule,
    the accepted attempt counting as the first (it opened the follow-up 15
    minutes out), so the follow-up's tries are 1 hour, 4 hours and 12
    hours apart."""
    assert RECIPIENT_RETRY_SECONDS == (900, 3600, 14400, 43200)
    for attempt, step in ((1, 1), (2, 2), (3, 3)):
        values, wait = address_retry.settled(
            row(attempt=attempt), result(Status.TRANSIENT, transient=(0, 1)), NOW
        )
        assert values["state"] == "waiting"
        assert values["remaining"] == [1, 3]
        assert wait == timedelta(seconds=RECIPIENT_RETRY_SECONDS[step])


def test_the_attempt_budget_ends_the_follow_up():
    values, _ = address_retry.settled(
        row(attempt=address_retry.MAX_ATTEMPTS),
        result(Status.TRANSIENT, transient=(0, 1)),
        NOW,
    )
    assert values["state"] == "failed" and values["reason"] == "attempts"


def test_limits_and_outages_are_spared_from_the_budget():
    """A sending limit waits for the limit and spends no counted attempt."""
    limited = result(Status.TRANSIENT, limit="rate")
    values, wait = address_retry.settled(
        row(attempt=address_retry.MAX_ATTEMPTS), limited, NOW
    )
    assert values["spared"] == 1 and values["state"] == "waiting"
    assert wait == timedelta(seconds=LIMIT_RETRY_SECONDS["rate"])
    outage = result(Status.UNAVAILABLE, health=ProviderHealth.UNAVAILABLE)
    assert address_retry.spared(outage)
    assert not address_retry.spared(result(Status.TRANSIENT, transient=(0,)))


def test_expiry_ends_rather_than_waiting_past_it():
    values, _ = address_retry.settled(
        row(days_left=0.001), result(Status.TRANSIENT, transient=(0, 1)), NOW
    )
    assert values["state"] == "failed" and values["reason"] == "expired"


@pytest.mark.parametrize("status", [Status.PERMANENT, Status.SYSTEMIC])
def test_a_whole_message_refusal_ends_the_follow_up(status):
    health = ProviderHealth.SYSTEMIC if status is Status.SYSTEMIC else None
    values, _ = address_retry.settled(row(), result(status, health=health), NOW)
    assert values["state"] == "failed" and values["reason"] == "provider_refused"
    assert values["remaining"] == [1, 3]


def test_an_uncertain_outcome_is_never_retried():
    """Whether it arrived is unknown, so it is never sent again."""
    values, _ = address_retry.settled(
        row(),
        result(Status.UNKNOWN, permanent=(0,), health=ProviderHealth.UNAVAILABLE),
        NOW,
    )
    assert values["state"] == "unknown"
    assert values["remaining"] == [3] and values["refused"] == [1]


def test_an_outcome_for_another_envelope_is_refused():
    with pytest.raises(PermissionError):
        address_retry.settled(row(), result(Status.ACCEPTED, count=3), NOW)


@pytest.mark.parametrize(
    ("mode", "purpose", "status", "transient", "expected"),
    [
        ("production", "initial", Status.ACCEPTED, (1,), True),
        ("production", "reminder", Status.ACCEPTED, (1,), True),
        ("production", "initial", Status.ACCEPTED, (), False),
        ("production", "receipt", Status.ACCEPTED, (1,), False),
        ("testing", "initial", Status.ACCEPTED, (1,), False),
    ],
)
def test_only_production_invitations_and_reminders_follow_up(
    mode, purpose, status, transient, expected
):
    message = SimpleNamespace(mode=mode, purpose=purpose, family_id="family")
    outcome = result(status, transient=transient)
    assert address_retry.follows_up(message, outcome) is expected
