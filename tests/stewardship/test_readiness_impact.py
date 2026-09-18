"""The go-live preview counts actual campaign decisions, not schedule rows."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from parishkit.stewardship.campaigns.readiness_impact import (
    FamilyImpact,
    FamilyImpactInput,
    summarize_family_impact,
)
from parishkit.stewardship.campaigns.schedule_recovery import RecoverySlot

NOW = datetime(2026, 10, 10, tzinfo=UTC)


def family(number, **changes):
    """Each complete Family group has an initial and two overdue reminders."""
    key = UUID(int=number)
    return replace(
        FamilyImpactInput(
            family_id=key,
            active=True,
            email_eligible=True,
            deliverable=True,
            responded=False,
            initial_delivered=False,
            slots=tuple(
                RecoverySlot(
                    occurrence_id=UUID(int=number * 100 + index),
                    definition_id=UUID(int=index),
                    kind=kind,
                    mode="production",
                    target=f"family:{key}",
                    slot="once",
                    due_at=NOW - timedelta(days=4 - index),
                    safely_cancellable=True,
                )
                for index, kind in enumerate(("initial", "reminder", "reminder"), 1)
            ),
        ),
        **changes,
    )


def test_five_thousand_families_stream_to_message_and_semantic_slot_totals():
    """No per-Family result list or multiplied reminder message count is needed."""
    impact = summarize_family_impact((family(n) for n in range(1, 5001)), cutoff=NOW)
    assert impact == FamilyImpact(
        families=5000,
        active=5000,
        email_eligible=5000,
        deliverable=5000,
        messages=5000,
        coalesced_slots=10000,
    )


def test_inactive_no_email_undeliverable_and_responded_are_distinct():
    """Counts retain the difference between contact eligibility and suppression."""
    impact = summarize_family_impact(
        (
            family(1, active=False),
            family(2, email_eligible=False),
            family(3, deliverable=False),
            family(4, responded=True),
            family(5),
        ),
        cutoff=NOW,
    )
    assert impact == FamilyImpact(
        families=5,
        active=4,
        email_eligible=3,
        no_eligible_email=1,
        deliverable=2,
        messages=1,
        coalesced_slots=2,
        skipped_slots=12,
    )


def test_prior_live_initial_coverage_and_future_reminders_do_not_inflate_counts():
    """Only one overdue reminder is live; a future slot is not coalesced early."""
    group = family(1)
    group = replace(
        group,
        initial_delivered=True,
        slots=(
            group.slots[1],
            replace(group.slots[2], due_at=NOW + timedelta(seconds=1)),
        ),
    )
    result = summarize_family_impact((group,), cutoff=NOW)
    assert result.messages == 1 and result.coalesced_slots == 0


def test_uncertain_delivery_is_visible_as_blocked_not_zero_due_proof():
    """A blocked Family does not cause unrelated Families to disappear."""
    group = family(1)
    group = replace(
        group,
        slots=(replace(group.slots[0], state="delivery_unknown"), *group.slots[1:]),
    )
    result = summarize_family_impact((group, family(2)), cutoff=NOW)
    assert result.messages == 1 and result.coalesced_slots == 2
    assert result.blocked_families == 1


def test_campaign_close_suppresses_all_family_messages():
    result = summarize_family_impact((family(1),), cutoff=NOW, closed=True)
    assert result.messages == 0 and result.skipped_slots == 3


def test_prestart_preview_and_empty_population_are_exact_zero_due():
    assert summarize_family_impact((), cutoff=NOW) == FamilyImpact()
    result = summarize_family_impact((family(1),), cutoff=NOW - timedelta(days=10))
    assert result.active == 1 and result.messages == result.coalesced_slots == 0


@pytest.mark.parametrize("numbers", [(1, 1), (2, 1)])
def test_repeated_or_out_of_order_family_aborts_entire_total(numbers):
    with pytest.raises(ValueError, match="UUID-ordered"):
        summarize_family_impact((family(n) for n in numbers), cutoff=NOW)


def test_reader_failure_never_returns_partial_success():
    """A later database page failure invalidates all earlier totals."""

    def interrupted():
        """Emulate a failed later page without involving a database or provider."""
        yield family(1)
        raise RuntimeError("reader unavailable")

    with pytest.raises(RuntimeError, match="reader unavailable"):
        summarize_family_impact(interrupted(), cutoff=NOW)


@pytest.mark.parametrize(
    "changes",
    [
        {"family_id": "1"},
        {"active": 1},
        {"slots": []},
        {"slots": (None,)},
        {"slots": family(1).slots * 34},
        {"slots": (replace(family(1).slots[0], mode="testing"),)},
        {"slots": (replace(family(1).slots[0], target="family:another"),)},
        {"slots": (replace(family(1).slots[0], kind="weekly_digest"),)},
    ],
)
def test_nonproduction_or_invalid_family_scope_is_rejected(changes):
    with pytest.raises(ValueError, match="Production Family group"):
        family(1, **changes)


@pytest.mark.parametrize(
    "options",
    [{"cutoff": NOW.replace(tzinfo=None)}, {"cutoff": None}, {"closed": 1}],
)
def test_empty_population_does_not_bypass_clock_validation(options):
    with pytest.raises(ValueError):
        summarize_family_impact((), **({"cutoff": NOW} | options))
