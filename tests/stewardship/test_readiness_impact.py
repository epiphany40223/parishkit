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
from parishkit.stewardship.campaigns.schedule_recovery import (
    RecoverySlot,
    plan_recovery,
)

NOW = datetime(2026, 10, 10, tzinfo=UTC)


def family(number, **changes):
    """Each complete Family group has an initial and two overdue reminders."""
    key = UUID(int=number)
    return replace(
        FamilyImpactInput(
            family_id=key,
            portal_eligible=True,
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
            family(1, portal_eligible=False),
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


def test_a_parishsoft_active_family_the_rule_leaves_out_changes_no_mail():
    """Go live's preview counts the rule's Families (#2331), the planner sends
    to ``active`` rows with an eligible email; both send the same mail.

    A Family active in ParishSoft but not chosen by the rule is stored with
    no portal and no email eligibility (email eligibility needs the rule),
    so the planner's ``active and email_eligible`` and the preview's
    ``portal_eligible and email_eligible`` both leave it out: the preview's
    messages and slots equal the planner's decisions for every Family.
    """
    groups = (
        family(1),
        family(2, portal_eligible=False, email_eligible=False, deliverable=False),
    )
    impact = summarize_family_impact(groups, cutoff=NOW)
    # Both Families are active in ParishSoft, as the planner reads them.
    planned = [
        plan_recovery(
            group.slots,
            cutoff=NOW,
            closed=False,
            eligible=group.email_eligible,
            responded=group.responded,
            deliverable=group.deliverable,
            initial_delivered=group.initial_delivered,
        )
        for group in groups
    ]
    assert impact.messages == sum(plan.selected is not None for plan in planned) == 1
    assert impact.coalesced_slots == sum(len(plan.coalesced) for plan in planned)
    assert impact.skipped_slots == sum(len(plan.skipped) for plan in planned) == 3
    assert (impact.active, impact.email_eligible, impact.deliverable) == (1, 1, 1)


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
        {"portal_eligible": 1},
        {"initial_unreviewed": 1},
        {"slots": []},
        {"slots": (None,)},
        {"slots": family(1).slots * 34},
        {"slots": (replace(family(1).slots[0], mode="testing"),)},
        {"slots": (replace(family(1).slots[0], target="family:another"),)},
        {"slots": (replace(family(1).slots[0], kind="report"),)},
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


@pytest.mark.parametrize(
    "changes,closed,blocked,skipped",
    [
        ({}, False, 1, 0),
        ({"initial_delivered": True}, False, 1, 0),
        ({"responded": True}, False, 0, 2),
        ({"portal_eligible": False}, False, 0, 2),
        ({"email_eligible": False}, False, 0, 2),
        ({"deliverable": False}, False, 0, 2),
        ({}, True, 0, 2),
    ],
)
def test_unreviewed_initial_only_blocks_otherwise_sendable_reminder(
    changes, closed, blocked, skipped
):
    """Dispatch holds must not erase the shared planner's skip dispositions."""
    group = family(1)
    group = replace(group, slots=group.slots[1:], initial_unreviewed=True, **changes)
    result = summarize_family_impact((group,), cutoff=NOW, closed=closed)
    assert result.messages == result.coalesced_slots == 0
    assert result.blocked_families == blocked
    assert result.skipped_slots == skipped
