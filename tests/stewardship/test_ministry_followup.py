"""Closed Ministry follow-up change grammar, validated without a database."""

from datetime import UTC, datetime
from urllib.parse import urlencode
from uuid import uuid4

import pytest
from django.http import QueryDict

from parishkit.stewardship.reports.ministries import STATE_LABELS
from parishkit.stewardship.reports.ministries import STATES as REPORT_STATES
from parishkit.stewardship.reports.ministry_followup import (
    HISTORY_STATES,
    STATES,
    FollowupQuery,
)
from parishkit.stewardship.reports.ministry_followup_views import (
    _refusal_error,
    change_values,
)
from parishkit.stewardship.workflows.followup import (
    MAX_CONTACT_NOTES,
    MAX_NOTES,
    FollowupRefusal,
    WorkflowChange,
    outcomes_for,
)
from parishkit.stewardship.workflows.models import STAFF_STATES

WHO, WHEN = uuid4(), datetime(2026, 9, 20, 12, tzinfo=UTC)
BASE = dict(state="in_progress", outcome=None, notes="")


@pytest.mark.parametrize(
    "values",
    [
        {},
        {"state": "new"},
        {"notes": "n" * MAX_NOTES},
        {"state": "resolved", "outcome": "joined"},
        {"state": "resolved", "outcome": "other", "notes": "Moved parishes"},
        {"state": "closed_no_response", "outcome": "no_response"},
        {"contact_channel": "phone", "contact_at": WHEN},
        {
            "contact_channel": "in_person",
            "contact_at": WHEN,
            "contact_notes": "c" * MAX_CONTACT_NOTES,
        },
    ],
)
def test_complete_workflow_is_accepted(values):
    """Every Staff-owned state, outcome pairing and contact shape round-trips."""
    change = WorkflowChange(**(BASE | values))
    assert all(getattr(change, key) == value for key, value in values.items())


@pytest.mark.parametrize(
    "values",
    [
        # Family- and worker-owned states are never a Staff edit.
        {"state": "cancelled"},
        {"state": "superseded"},
        {"state": "unknown"},
        # An outcome belongs to exactly its closed state.
        {"outcome": "joined"},
        {"state": "resolved"},
        {"state": "resolved", "outcome": "no_response"},
        {"state": "closed_no_response", "outcome": "declined"},
        {"state": "resolved", "outcome": "invented"},
        {"state": "resolved", "outcome": "other"},
        {"state": "resolved", "outcome": "other", "notes": "   "},
        # Follow-up has no assignee (#552): neither the state nor the field.
        {"state": "assigned"},
        # A contact attempt is complete, zoned, bounded and from a closed set.
        {"contact_channel": "phone"},
        {"contact_at": WHEN},
        {"contact_notes": "Notes without an attempt"},
        {"contact_channel": "fax", "contact_at": WHEN},
        {"contact_channel": "phone", "contact_at": WHEN.replace(tzinfo=None)},
        {"contact_channel": "phone", "contact_at": "2026-09-20"},
        {
            "contact_channel": "phone",
            "contact_at": WHEN,
            "contact_notes": "c" * (MAX_CONTACT_NOTES + 1),
        },
        {"notes": "n" * (MAX_NOTES + 1)},
        {"notes": "nul\x00byte"},
        {"notes": None},
    ],
)
def test_incomplete_or_foreign_workflow_is_rejected(values):
    """A malformed form is a client error before any lock or query."""
    with pytest.raises(ValueError):
        WorkflowChange(**(BASE | values))


def test_change_has_no_assignee_field():
    """An assignee cannot even be expressed in a change (#552)."""
    with pytest.raises(TypeError):
        WorkflowChange(**BASE, assignee_id=WHO)


FORM = {
    "expected_version": "3",
    "request_key": str(WHO),
    "state": "new",
    "outcome": "",
    "notes": "Called\r\nagain",
    "contact_channel": "",
    "contact_date": "",
    "contact_time": "",
    "contact_notes": "",
}


def form(values):
    """A native form body, as Django parses one."""
    return QueryDict(urlencode(values))


def test_edit_form_has_no_assignee():
    """The edit form's closed grammar no longer has, or accepts, an assignee."""
    values = change_values(form(FORM))
    assert values["change"] == WorkflowChange(
        state="new", outcome=None, notes="Called\nagain"
    )
    for invalid in (
        FORM | {"assignee": ""},
        FORM | {"assignee": str(WHO)},
        FORM | {"state": "assigned"},
    ):
        with pytest.raises(ValueError):
            change_values(form(invalid))


# What the page sends with JavaScript: hidden fields are disabled, so only
# the always-shown ones arrive.
MINIMAL = {
    key: FORM[key]
    for key in ("expected_version", "request_key", "state", "notes", "contact_channel")
}
CONTACT = {"contact_date": "2026-09-19", "contact_time": "15:04"}


def change(values):
    """The WorkflowChange a form body parses to."""
    return change_values(form(values))["change"]


@pytest.mark.parametrize(
    ("values", "state", "outcome"),
    [
        # Notes are optional, and hidden fields may be absent entirely.
        (MINIMAL | {"notes": ""}, "new", None),
        (MINIMAL | {"state": "in_progress", "notes": ""}, "in_progress", None),
        # An open status has no outcome; a stray one (no JavaScript) is ignored.
        (FORM | {"state": "in_progress", "outcome": "joined"}, "in_progress", None),
        # Only Resolved takes a chosen outcome.
        (MINIMAL | {"state": "resolved", "outcome": "joined"}, "resolved", "joined"),
        (FORM | {"state": "resolved", "outcome": "declined"}, "resolved", "declined"),
        # Closed: no response always records No response, whatever was sent.
        (
            MINIMAL | {"state": "closed_no_response"},
            "closed_no_response",
            "no_response",
        ),
        (
            FORM | {"state": "closed_no_response", "outcome": "joined"},
            "closed_no_response",
            "no_response",
        ),
    ],
)
def test_outcome_applies_only_where_the_status_takes_one(values, state, outcome):
    """Outcome is required for Resolved only; elsewhere it is derived or none."""
    parsed = change(values)
    assert (parsed.state, parsed.outcome) == (state, outcome)


def test_contact_details_apply_only_with_a_channel():
    """No channel: date, time and notes are ignored, present or not."""
    for values in (
        MINIMAL,
        FORM | CONTACT | {"contact_notes": "Typed, then cleared the channel"},
    ):
        parsed = change(values)
        assert (parsed.contact_channel, parsed.contact_at, parsed.contact_notes) == (
            None,
            None,
            "",
        )
    parsed = change(MINIMAL | CONTACT | {"contact_channel": "phone"})
    assert parsed.contact_at == datetime(2026, 9, 19, 15, 4, tzinfo=UTC)
    assert parsed.contact_notes == ""
    parsed = change(
        FORM | CONTACT | {"contact_channel": "email", "contact_notes": "Hi"}
    )
    assert (parsed.contact_channel, parsed.contact_notes) == ("email", "Hi")


@pytest.mark.parametrize(
    "values",
    [
        MINIMAL | {"state": "resolved"},  # Resolved needs its outcome.
        FORM | {"state": "resolved"},
        MINIMAL | {"state": "resolved", "outcome": "no_response"},
        MINIMAL
        | {"state": "resolved", "outcome": "other", "notes": ""},  # Other needs notes.
        MINIMAL | {"contact_channel": "phone"},  # A contact needs date and time.
        MINIMAL | {"contact_channel": "phone", "contact_date": "2026-09-19"},
        {key: value for key, value in MINIMAL.items() if key != "notes"},
        {key: value for key, value in MINIMAL.items() if key != "contact_channel"},
        MINIMAL | {"unexpected": "field"},
    ],
)
def test_incomplete_follow_up_forms_are_refused(values):
    """What does apply must be complete; unknown or missing core fields are refused."""
    with pytest.raises(ValueError):
        change(values)


def test_assigned_is_no_longer_offered_but_still_reads_as_new():
    """Edits, queue and report filters drop Assigned; stored ones read as New."""
    assert "assigned" not in STAFF_STATES
    assert "assigned" not in STATES and "assigned" not in REPORT_STATES
    assert STATE_LABELS["assigned"] == STATE_LABELS["new"] == "New"
    # Past history keeps what each edit recorded.
    assert HISTORY_STATES["assigned"] == "Assigned"
    assert "assignee" not in FollowupQuery().form_values()
    for invalid in ({"assignee": "any"}, {"state": "assigned"}):
        with pytest.raises(ValueError):
            FollowupQuery.parse(invalid)


def test_each_request_kind_is_offered_only_the_outcomes_it_can_record():
    """A join cannot record Left ministry and a leave cannot record Joined."""
    assert outcomes_for("join") == ("joined", "declined", "duplicate", "other")
    assert outcomes_for("leave") == (
        "leave_confirmed",
        "declined",
        "duplicate",
        "other",
    )


@pytest.mark.parametrize(
    ("values", "code"),
    [
        (MINIMAL | {"state": "resolved"}, "outcome_required"),
        (
            MINIMAL | {"state": "resolved", "outcome": "other", "notes": "  "},
            "other_needs_notes",
        ),
        (MINIMAL | {"contact_channel": "phone"}, "contact_incomplete"),
        (
            MINIMAL | {"contact_channel": "phone", "contact_date": "2026-09-19"},
            "contact_incomplete",
        ),
    ],
)
def test_correctable_mistakes_are_named(values, code):
    """Each mistake a person can fix raises its own refusal code."""
    with pytest.raises(FollowupRefusal) as refused:
        change(values)
    assert refused.value.code == code


def test_refusals_name_the_one_problem_and_its_field():
    """The in-place summary says exactly what is wrong and links to the field."""
    kind = _refusal_error(
        FollowupRefusal("outcome_kind", outcome="leave_confirmed", action="join")
    )
    assert str(kind["message"]) == "Left ministry doesn't apply to a request to join."
    assert kind["field_id"] == "followup-outcome"
    assert _refusal_error(FollowupRefusal("contact_future"))["field_id"] == (
        "contact-date"
    )
    # A malformed form is not a correctable refusal.
    with pytest.raises(ValueError) as refused:
        change({key: value for key, value in MINIMAL.items() if key != "notes"})
    assert not isinstance(refused.value, FollowupRefusal)
