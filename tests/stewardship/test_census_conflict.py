"""An Administrator's census conflict resolution, without a database (#528).

"Use the Family's value anyway" (ADM-09.03) is tested with the real roles
in ``database/test_census_conflict_postgresql.py``; these tests cover which
rows offer it, the planner's accepted base, reconciliation's approval reset
and the page's form parsing.
"""

from types import SimpleNamespace

import pytest
from django.http import QueryDict

from parishkit.stewardship.reports.census_change_views import _bulk, _resolution
from parishkit.stewardship.reports.census_changes import shape
from parishkit.stewardship.responses.census_review import resolvable
from parishkit.stewardship.responses.publication import Outcome, plan_entity
from parishkit.stewardship.responses.reconciliation import resets_approval

from .test_census_changes import row
from .test_census_edit import page
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
        ({"execution": "queued"}, False),
        ({"execution": "conflict", "handling": "manual", "field": "suffix"}, False),
        (
            {"execution": "conflict", "entity_kind": "family", "field": "home_address"},
            False,
        ),
    ],
)
def test_only_a_known_automatic_conflict_is_resolvable(values, expected):
    """The service's gate and the row's button agree."""
    shown = shape(row(**values))
    assert shown["resolve_conflict"] is expected
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


def test_the_review_form_sends_use_the_familys_value_anyway():
    """The button's value reaches the service as its own action, with the
    form's note and keys, and never with an edited value."""
    base = (
        "proposal=00000000-0000-4000-8000-000000000001&version=3"
        "&request_key=00000000-0000-4000-8000-000000000002&note=+Asked+"
    )
    parameters = QueryDict(f"resolve=use_family&{base}", mutable=True)
    parsed = _resolution(parameters)
    assert (parsed["action"], parsed["note"]) == ("use_family", "Asked")
    assert "value" not in parsed and not parameters
    with pytest.raises(ValueError):
        _resolution(QueryDict(f"resolve=use_family&value=Pat&{base}", mutable=True))


def test_it_is_never_sent_in_bulk():
    """The bulk bar's post takes only Review decisions: a bulk Use the
    Family's value anyway is a malformed form (a 400)."""
    form = (
        "bulk=use_family&bulk_key=00000000-0000-4000-8000-000000000003"
        "&bulk_note=&change=00000000-0000-4000-8000-000000000001:3"
    )
    with pytest.raises(ValueError):
        _bulk(QueryDict(form, mutable=True))


@pytest.mark.parametrize(
    ("edited", "label", "writes"),
    [
        (False, "Use the Family's value anyway", "the Family's answer"),
        (True, "Use the edited value anyway", "the edited value"),
    ],
)
def test_the_button_and_its_help_line_name_what_is_written(edited, label, writes):
    """A Conflict row's Review draws the button for what publication will
    write, with the help line above it, describing it."""
    shown = shape(
        row(
            execution="conflict",
            admin_value_set=edited,
            admin_value="555-0123" if edited else None,
        )
    )
    shown["conflict_describedby"] = "who-1 what-1 conflict-1"
    html = page([shown])
    help_at = html.index('<p class="help" id="conflict-1">')
    button_at = html.index('value="use_family"')
    assert help_at < button_at
    assert f"{label}</button>" in html.replace("\n", "")
    assert f"publication writes {writes} over the value in ParishSoft now" in html
    assert 'aria-describedby="who-1 what-1 conflict-1"' in html
