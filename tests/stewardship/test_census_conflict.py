"""An Administrator's census conflict resolution, without a database (#528).

"Use the Family's value anyway" (ADM-09.03) is tested with the real roles
in ``database/test_census_conflict_postgresql.py``; these tests cover which
rows offer it, the planner's accepted base, reconciliation's approval reset
and the page's form parsing.
"""

from types import SimpleNamespace

import pytest

from parishkit.stewardship.reports.census_changes import shape
from parishkit.stewardship.responses.census_review import resolvable
from parishkit.stewardship.responses.publication import Outcome, plan_entity
from parishkit.stewardship.responses.reconciliation import resets_approval

from .test_census_changes import row
from .test_response_publication import outcomes, payload, proposal


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ({"execution": "conflict"}, True),
        ({"execution": "conflict", "decision": "approved"}, True),
        ({"execution": "conflict", "decision": "ignored"}, True),
        ({"execution": "conflict", "admin_value_set": True}, True),
        # ParishSoft's value is unknown: nothing to accept yet.
        (
            {
                "execution": "conflict",
                "current_available": False,
                "current_value": None,
            },
            False,
        ),
        ({"execution": "pending"}, False),
        ({"execution": "failed"}, False),
        ({"execution": "queued", "decision": "approved"}, False),
        # A By hand Conflict is resolvable too since #1683 (the worklist's
        # Accept); pk-admin's row offer stays an Automatic change's.
        ({"execution": "conflict", "handling": "manual", "field": "suffix"}, True),
        (
            {"execution": "conflict", "entity_kind": "family", "field": "home_address"},
            True,
        ),
    ],
)
def test_only_a_known_conflict_is_resolvable(values, expected):
    """The service's gate and the row's offer agree."""
    shown = shape(row(**values))
    assert shown["resolvable"] is expected
    assert shown["resolve_conflict"] is (expected and shown["automatic"])
    assert resolvable(SimpleNamespace(**(row() | values))) is expected


def test_the_planner_merges_a_resolved_conflict_against_parishsoft_now():
    """With an active accepted base the proposal is written while ParishSoft
    still holds the accepted value; otherwise the Family's baseline rules,
    which makes the same read a conflict (the annotation fails safe)."""
    resolved = proposal(
        "first_name",
        "Requested",
        "Original",
        current_value="Parish",
        conflict_accepted=True,
    )
    plan = plan_entity("member", "41", [resolved], payload(firstName="Parish"))
    assert outcomes(plan) == {"first_name": Outcome.WRITE}
    assert plan.payload["firstName"] == "Requested"
    for read, outcome in (
        ("Elsewhere", Outcome.CONFLICT),
        ("Requested", Outcome.ALREADY_IN_PARISHSOFT),
        # The baseline itself no longer counts once a base is accepted.
        ("Original", Outcome.CONFLICT),
    ):
        plan = plan_entity("member", "41", [resolved], payload(firstName=read))
        assert outcomes(plan) == {"first_name": outcome}
    unresolved = proposal("first_name", "Requested", "Original", current_value="Parish")
    plan = plan_entity("member", "41", [unresolved], payload(firstName="Parish"))
    assert outcomes(plan) == {"first_name": Outcome.CONFLICT}


@pytest.mark.parametrize(
    ("before", "decision", "accepted", "after", "expected"),
    [
        ("pending", "approved", True, "conflict", True),
        ("pending", "approved", True, "pending", True),
        ("failed", "approved", True, "conflict", True),
        # A final outcome keeps the decision it had.
        ("pending", "approved", True, "resolved_upstream", False),
        ("pending", "approved", True, "cancelled", False),
        # The publish worker owns a queued change.
        ("queued", "approved", True, "conflict", False),
        # Only an approval resting on an accepted base.
        ("pending", "approved", False, "conflict", False),
        ("pending", "ignored", True, "conflict", False),
        ("pending", "unreviewed", True, "conflict", False),
    ],
)
def test_reconciliation_withdraws_only_an_accepted_approval(
    before, decision, accepted, after, expected
):
    """A new ParishSoft value sends back an approval given over the old one."""
    reconciled = SimpleNamespace(
        execution=before, decision=decision, conflict_accepted=accepted
    )
    assert resets_approval(reconciled, after) is expected


@pytest.mark.parametrize(
    ("after", "expected"), [("pending", False), ("conflict", True)]
)
def test_a_caught_up_change_keeps_its_accepted_approval(after, expected):
    """Strict phase order (#1890): ParishSoft holding the carried value
    leaves the change open with nothing to write, so its approval stays; an
    unusable read (a Conflict) still sends it back."""
    reconciled = SimpleNamespace(
        execution="pending", decision="approved", conflict_accepted=True
    )
    assert resets_approval(reconciled, after, caught_up=True) is expected
