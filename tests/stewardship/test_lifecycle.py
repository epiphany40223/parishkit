"""Exhaustive lifecycle/date predicates; these do not simulate readiness evidence."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from itertools import product
from uuid import uuid4

import pytest

from parishkit.stewardship.campaigns.domain import CampaignState as State
from parishkit.stewardship.campaigns.domain import SystemMode as Mode
from parishkit.stewardship.campaigns.domain import UTCInterval
from parishkit.stewardship.campaigns.lifecycle import (
    TRANSITIONS,
    Action,
    CampaignFacts,
    draft_creation_admitted,
    portal_admitted,
    scheduled_work_admitted,
    structural_edit_admitted,
    transition_audit_event,
    transition_target,
)

INTERVAL = UTCInterval(
    datetime(2026, 10, 1, tzinfo=UTC), datetime(2026, 11, 1, tzinfo=UTC)
)


@pytest.mark.parametrize(
    "state,mode,current,restore",
    list(product(State, Mode, (True, False), (True, False))),
)
def test_portal_boundary_matrix(state, mode, current, restore):
    """All modes/states fail closed at the exact close even if scheduler state lags."""
    facts = CampaignFacts(state, mode, INTERVAL, current, restore)
    allowed = (
        current
        and not restore
        and (
            state is State.DRAFT
            if mode is Mode.TESTING
            else state in {State.SCHEDULED, State.ACTIVE}
        )
    )
    for now, in_range in [
        (INTERVAL.start - timedelta(microseconds=1), False),
        (INTERVAL.start, True),
        (INTERVAL.end - timedelta(microseconds=1), True),
        (INTERVAL.end, False),
    ]:
        assert portal_admitted(facts, now) == (allowed and in_range)


@pytest.mark.parametrize("state,action", list(product(State, Action)))
def test_transition_registry_rejects_unlisted_sources(state, action):
    """Every source/action pairing consults the same explicit registry."""
    facts = CampaignFacts(state, Mode.PRODUCTION, INTERVAL, True)
    target = transition_target(action, facts, INTERVAL.start)
    if state not in TRANSITIONS[action].sources:
        assert target is None
    elif target is not None:
        assert target in TRANSITIONS[action].targets
    rule = TRANSITIONS[action]
    assert transition_audit_event(action) == "campaign_" + action.value
    if rule.actor == "administrator":
        assert rule.reauthentication and rule.confirmation


def test_activation_withdrawal_close_and_reopen_races():
    """Time is checked at execution, not inferred from a stale lifecycle flag."""
    draft = CampaignFacts(State.DRAFT, Mode.TESTING, INTERVAL, True)
    assert (
        transition_target(Action.ACTIVATE, draft, INTERVAL.start - timedelta(seconds=1))
        is State.SCHEDULED
    )
    assert transition_target(Action.ACTIVATE, draft, INTERVAL.start) is State.ACTIVE
    assert transition_target(Action.ACTIVATE, draft, INTERVAL.end) is None
    scheduled = replace(draft, state=State.SCHEDULED, mode=Mode.PRODUCTION)
    assert (
        transition_target(
            Action.WITHDRAW, scheduled, INTERVAL.start - timedelta(seconds=1)
        )
        is State.DRAFT
    )
    assert transition_target(Action.WITHDRAW, scheduled, INTERVAL.start) is None
    assert transition_target(Action.START, scheduled, INTERVAL.start) is State.ACTIVE
    assert transition_target(Action.START, scheduled, INTERVAL.end) is None
    assert transition_target(Action.CLOSE, scheduled, INTERVAL.end) is State.CLOSED
    assert transition_target(Action.CLOSE, scheduled, INTERVAL.start) is None
    closed = replace(scheduled, state=State.CLOSED)
    extended = UTCInterval(INTERVAL.start, INTERVAL.end + timedelta(days=1))
    assert (
        transition_target(
            Action.REOPEN, closed, INTERVAL.end, proposed_interval=extended
        )
        is State.ACTIVE
    )
    for interval in (
        None,
        INTERVAL,
        UTCInterval(INTERVAL.start - timedelta(days=1), extended.end),
    ):
        assert (
            transition_target(
                Action.REOPEN, closed, INTERVAL.end, proposed_interval=interval
            )
            is None
        )
    assert (
        transition_target(
            Action.REOPEN, closed, extended.end, proposed_interval=extended
        )
        is None
    )


def test_historical_restore_and_work_holds():
    """Pause is independent of portal access; archive history never becomes current."""
    active = CampaignFacts(
        State.ACTIVE, Mode.PRODUCTION, INTERVAL, True, delivery_paused=True
    )
    assert portal_admitted(active, INTERVAL.start)
    assert scheduled_work_admitted(active, INTERVAL.start)
    assert not scheduled_work_admitted(active, INTERVAL.start, delivery=True)
    assert not scheduled_work_admitted(
        replace(active, catch_up_pending=True), INTERVAL.start
    )
    assert not scheduled_work_admitted(replace(active, current=False), INTERVAL.start)
    archived = replace(active, state=State.ARCHIVED)
    assert transition_target(Action.UNARCHIVE, archived, INTERVAL.end) is State.CLOSED
    assert (
        transition_target(
            Action.UNARCHIVE, replace(archived, current=False), INTERVAL.end
        )
        is None
    )
    assert (
        transition_target(
            Action.UNARCHIVE, replace(archived, restore_required=True), INTERVAL.end
        )
        is None
    )
    assert transition_target(Action.PURGE, archived, INTERVAL.end) is None
    assert (
        transition_target(
            Action.PURGE,
            replace(archived, mode=Mode.TESTING, current=False),
            INTERVAL.end,
        )
        is State.PURGING
    )
    assert (
        transition_target(
            Action.PURGE, replace(archived, mode=Mode.TESTING), INTERVAL.end
        )
        is None
    )


@pytest.mark.parametrize("state", State)
def test_successor_and_structural_locks(state):
    """Every unfinished purge request blocks creation, even while lifecycle is
    archived.
    """
    expected = state in {State.ARCHIVED, State.PURGED}
    kwargs = dict(
        mode=Mode.TESTING, current_id=None, states=[state], nonterminal_purge=False
    )
    assert draft_creation_admitted(**kwargs) == expected
    assert not draft_creation_admitted(**(kwargs | {"nonterminal_purge": True}))
    assert not draft_creation_admitted(**(kwargs | {"current_id": uuid4()}))
    assert not draft_creation_admitted(**(kwargs | {"mode": Mode.PRODUCTION}))
    assert structural_edit_admitted(state, ever_active=False, locked=False) == (
        state is State.DRAFT
    )
    assert not structural_edit_admitted(state, ever_active=True, locked=False)
    assert not structural_edit_admitted(state, ever_active=False, locked=True)


def test_noncanonical_facts_fail():
    """Strings, truthy integers and naive instants cannot impersonate domain values."""
    facts = CampaignFacts(State.DRAFT, Mode.TESTING, INTERVAL, True)
    for changes in (
        {"state": "draft"},
        {"mode": "testing"},
        {"interval": None},
        {"current": 1},
    ):
        with pytest.raises(ValueError):
            replace(facts, **changes)
    with pytest.raises(ValueError):
        transition_target("activate", facts, INTERVAL.start)
    with pytest.raises(ValueError):
        transition_audit_event("activate")
    with pytest.raises(ValueError):
        portal_admitted(facts, datetime(2026, 10, 1))
    with pytest.raises(ValueError):
        draft_creation_admitted(
            mode="testing", current_id=None, states=[], nonterminal_purge=False
        )
    with pytest.raises(ValueError):
        draft_creation_admitted(
            mode=Mode.TESTING,
            current_id=None,
            states=["draft"],
            nonterminal_purge=False,
        )
    with pytest.raises(ValueError):
        structural_edit_admitted(State.DRAFT, ever_active=1, locked=False)
