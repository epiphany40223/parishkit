"""Recovery chooses one message without recalling or retrying terminal work."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from itertools import permutations
from uuid import UUID

import pytest

from parishkit.stewardship.campaigns.schedule_recovery import (
    RecoveryPlan,
    RecoverySlot,
    plan_recovery,
)

NOW = datetime(2026, 10, 10, tzinfo=UTC)


def slot(number, *, kind="reminder", **values):
    """Deterministic nonprivate metadata makes order/identity assertions readable."""
    return replace(
        RecoverySlot(
            occurrence_id=UUID(int=number),
            definition_id=UUID(int=number + 100),
            kind=kind,
            mode="production",
            target="family:1",
            slot="once",
            due_at=NOW - timedelta(days=10 - number),
            safely_cancellable=True,
        ),
        **values,
    )


def test_overdue_initial_wins_without_consuming_future_or_held_reminder():
    rows = (
        slot(1, kind="initial"),
        slot(2),
        slot(3),
        slot(4, due_at=NOW + timedelta(seconds=1)),
        slot(5, held=True),
    )
    expected = RecoveryPlan(
        selected=rows[0].occurrence_id,
        coalesced=(rows[1].occurrence_id, rows[2].occurrence_id),
        reason="missed_family_recovery",
    )
    for ordering in permutations(rows):
        assert plan_recovery(ordering, cutoff=NOW) == expected


def test_latest_overdue_reminder_wins_after_initial_fulfillment():
    rows = tuple(slot(number) for number in range(1, 4))
    plan = plan_recovery(rows, cutoff=NOW, initial_delivered=True)
    assert plan.selected == rows[-1].occurrence_id
    assert plan.coalesced == tuple(row.occurrence_id for row in rows[:-1])


@pytest.mark.parametrize("state", ["failed", "skipped", "coalesced"])
def test_unsuccessful_initial_holds_reminders_without_retrying_the_initial(state):
    """A pending reminder cannot stand in for an unresolved initial failure."""
    rows = (slot(1, kind="initial", state=state), slot(2), slot(3))
    assert plan_recovery(rows, cutoff=NOW) == RecoveryPlan(blocked=True)
    assert plan_recovery(rows, cutoff=NOW, initial_delivered=True).selected == (
        rows[-1].occurrence_id
    )
    closed = plan_recovery(rows, cutoff=NOW, closed=True)
    assert closed.skipped == tuple(row.occurrence_id for row in rows[1:])
    assert not closed.blocked


def test_equal_due_reminders_have_stable_definition_tiebreak():
    rows = (slot(1, due_at=NOW), slot(2, due_at=NOW))
    assert plan_recovery(rows, cutoff=NOW) == plan_recovery(
        tuple(reversed(rows)), cutoff=NOW
    )
    assert plan_recovery(rows, cutoff=NOW).selected == rows[1].occurrence_id


@pytest.mark.parametrize(
    "field,reason",
    [
        ("closed", "campaign_closed"),
        ("eligible", "family_ineligible"),
        ("responded", "family_responded"),
        ("deliverable", "no_deliverable_recipient"),
    ],
)
def test_inapplicable_family_work_has_reasoned_skips(field, reason):
    rows = (slot(1, kind="initial"), slot(2))
    plan = plan_recovery(rows, cutoff=NOW, **{field: field in {"closed", "responded"}})
    assert plan.skipped == tuple(row.occurrence_id for row in rows)
    assert plan.reason == reason and plan.selected is None and not plan.coalesced


@pytest.mark.parametrize("state", ["pending", "running", "delivery_unknown"])
def test_uncertain_work_holds_whole_group_even_when_other_rows_are_cancellable(state):
    rows = (slot(1, state=state, safely_cancellable=False), slot(2))
    assert plan_recovery(rows, cutoff=NOW) == RecoveryPlan(blocked=True)
    assert plan_recovery(rows, cutoff=NOW, closed=True) == RecoveryPlan(blocked=True)


@pytest.mark.parametrize("state", ["succeeded", "failed", "skipped", "coalesced"])
def test_terminal_work_is_never_automatically_retried_or_rewritten(state):
    rows = (slot(1, state=state, safely_cancellable=False), slot(2))
    assert plan_recovery(rows, cutoff=NOW) == RecoveryPlan(
        selected=rows[1].occurrence_id
    )


def test_missing_cancellation_proof_defaults_to_held_not_permission():
    row = RecoverySlot(
        UUID(int=1), UUID(int=2), "initial", "testing", "family:1", "once", NOW
    )
    assert plan_recovery((row,), cutoff=NOW).blocked


def test_daily_backlog_requires_new_recovery_with_every_original_date():
    definition = UUID(int=100)
    rows = tuple(
        slot(
            number,
            kind="daily_digest",
            definition_id=definition,
            slot=f"2026-10-{number:02}",
            target="admins",
        )
        for number in range(1, 4)
    )
    plan = plan_recovery(
        rows, cutoff=NOW, closed=True, eligible=False, responded=True, deliverable=False
    )
    assert plan.selected is None and plan.reason == "missed_daily_recovery"
    assert plan.coalesced == tuple(row.occurrence_id for row in rows)
    assert tuple(day.isoformat() for day in plan.daily_dates) == tuple(
        row.slot for row in rows
    )
    assert plan_recovery(tuple(reversed(rows)), cutoff=NOW) == plan


def test_one_daily_slot_remains_an_ordinary_digest():
    row = slot(1, kind="daily_digest", slot="2026-10-01", target="admins")
    assert plan_recovery((row,), cutoff=NOW) == RecoveryPlan(selected=row.occurrence_id)


def test_weekly_backlog_uses_latest_interval_without_new_recipient_identity():
    rows = tuple(
        slot(
            number,
            kind="weekly_digest",
            definition_id=UUID(int=100),
            slot=f"2026-10-{number:02}",
            target="admins",
        )
        for number in range(1, 4)
    )
    plan = plan_recovery(rows, cutoff=NOW)
    assert plan.selected == rows[-1].occurrence_id
    assert plan.reason == "missed_weekly_recovery" and not plan.daily_dates


def test_empty_future_and_held_groups_have_no_mutations():
    assert plan_recovery((), cutoff=NOW) == RecoveryPlan()
    assert (
        plan_recovery((slot(1, due_at=NOW + timedelta(seconds=1)),), cutoff=NOW)
        == RecoveryPlan()
    )
    assert (
        plan_recovery((slot(1, held=True, state="delivery_unknown"),), cutoff=NOW)
        == RecoveryPlan()
    )


@pytest.mark.parametrize(
    "values",
    [
        {"mode": "testing"},
        {"target": "family:another"},
        {"kind": "weekly_digest"},
        {"occurrence_id": UUID(int=1)},
        {"definition_id": UUID(int=101)},
    ],
)
def test_group_rejects_mixed_identity_and_duplicate_obligations(values):
    with pytest.raises(ValueError, match="semantic group"):
        plan_recovery((slot(1), slot(2, **values)), cutoff=NOW)


def test_two_initials_and_fulfilled_initial_are_not_recovery_candidates():
    with pytest.raises(ValueError):
        plan_recovery((slot(1, kind="initial"), slot(2, kind="initial")), cutoff=NOW)
    with pytest.raises(ValueError, match="Fulfilled initial"):
        plan_recovery((slot(1, kind="initial"),), cutoff=NOW, initial_delivered=True)


@pytest.mark.parametrize("value", ["yesterday", "20261001", "2026-02-30"])
def test_daily_slots_require_canonical_dates(value):
    with pytest.raises(ValueError):
        plan_recovery((slot(1, kind="daily_digest", slot=value),), cutoff=NOW)


@pytest.mark.parametrize(
    "values",
    [
        {"occurrence_id": "id"},
        {"definition_id": None},
        {"kind": []},
        {"mode": {}},
        {"state": "retry_wait"},
        {"due_at": NOW.replace(tzinfo=None)},
        {"target": ""},
        {"slot": "x" * 129},
        {"held": 1},
        {"safely_cancellable": "yes"},
    ],
)
def test_slot_rejects_invalid_metadata(values):
    with pytest.raises(ValueError):
        slot(1, **values)


@pytest.mark.parametrize(
    "rows,options",
    [
        ([], {}),
        ((None,), {}),
        ((slot(1),) * 1001, {}),
        ((), {"cutoff": NOW.replace(tzinfo=None)}),
        ((), {"eligible": 1}),
    ],
)
def test_plan_requires_bounded_typed_complete_group(rows, options):
    with pytest.raises(ValueError):
        plan_recovery(rows, **({"cutoff": NOW} | options))
