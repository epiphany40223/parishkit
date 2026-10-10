"""Closed Ministry follow-up change grammar, validated without a database."""

import re
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlencode
from uuid import uuid4

import pytest
from django.http import QueryDict
from django.template.loader import render_to_string

from parishkit.stewardship.reports.ministries import STATE_LABELS
from parishkit.stewardship.reports.ministries import STATES as REPORT_STATES
from parishkit.stewardship.reports.ministry_followup import (
    HISTORY_STATES,
    STATES,
    FollowupQuery,
)
from parishkit.stewardship.reports.ministry_followup_views import (
    FIELD_IDS,
    REFUSALS,
    _refusal_error,
    change_values,
    refusal_fields,
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
    "contact_zone": "",
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
# The browser sends its zone with a contact attempt (#558).
CONTACT = {"contact_date": "2026-09-19", "contact_time": "15:04", "contact_zone": "UTC"}


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
        FollowupRefusal("outcome_kind", outcome="leave_confirmed", action="join"), {}
    )
    assert str(kind["message"]) == "Left ministry doesn't apply to a request to join."
    assert kind["field_id"] == "followup-outcome"
    assert kind["fields"] == ("outcome",)
    future = _refusal_error(FollowupRefusal("contact_future"), {})
    assert future["field_id"] == "contact-date"
    assert future["fields"] == ("contact_date", "contact_time")
    # A malformed form is not a correctable refusal.
    with pytest.raises(ValueError) as refused:
        change({key: value for key, value in MINIMAL.items() if key != "notes"})
    assert not isinstance(refused.value, FollowupRefusal)


SOURCES = Path(__file__).parents[2] / "src/parishkit/stewardship"


def test_every_refusal_code_names_its_fields():
    """Every FollowupRefusal code raised anywhere has one entry naming the
    form fields it concerns, and each of those fields has an element id."""
    raised = {
        code
        for path in (
            SOURCES / "workflows/followup.py",
            SOURCES / "reports/ministry_followup_views.py",
        )
        for code in re.findall(r'FollowupRefusal\(\s*"(\w+)"', path.read_text())
    }
    assert raised == set(REFUSALS)
    expected = {
        "outcome_required": ("outcome",),
        "outcome_kind": ("outcome",),
        "other_needs_notes": ("notes",),
        "contact_incomplete": ("contact_date", "contact_time"),
        "contact_future": ("contact_date", "contact_time"),
        "contact_zone": ("contact_date", "contact_time"),
        "contact_time": ("contact_time",),
    }
    assert {code: fields for code, (_, fields) in REFUSALS.items()} == expected
    assert {name for _, fields in REFUSALS.values() for name in fields} == set(
        FIELD_IDS
    )


@pytest.mark.parametrize(
    ("typed", "message"),
    [
        ("25:00", "“25:00” has no hour 25: hours run from 0 to 23."),
        ("2:30 pmx", "“2:30 pmx” isn't a time. Try 2:00 PM, 2pm, 14:00 or 1400."),
        (
            "14:30:15",
            "“14:30:15” has seconds: these times are to the minute, so "
            "leave the seconds out.",
        ),
    ],
)
def test_unreadable_contact_time_is_refused_at_the_time_field(typed, message):
    """A contact time the shared time-of-day parser refuses (#398) is shown
    in place at Time alone, in the parser's own words."""
    with pytest.raises(FollowupRefusal) as refused:
        change(
            MINIMAL | {"contact_channel": "phone"} | CONTACT | {"contact_time": typed}
        )
    error = _refusal_error(refused.value, {})
    assert error["code"] == "contact_time"
    assert str(error["message"]) == message
    assert error["fields"] == ("contact_time",)
    assert error["field_id"] == "contact-time"


def test_contact_time_longer_than_the_field_is_a_malformed_form():
    """The page bounds the entry at 32 characters; more is not a refusal."""
    with pytest.raises(ValueError) as refused:
        change(
            MINIMAL
            | {"contact_channel": "phone"}
            | CONTACT
            | {"contact_time": "1" * 33}
        )
    assert not isinstance(refused.value, FollowupRefusal)


@pytest.mark.parametrize(
    ("submitted", "fields"),
    [
        ({"contact_date": "", "contact_time": "10:00"}, ("contact_date",)),
        ({"contact_date": "2026-09-19", "contact_time": " "}, ("contact_time",)),
        ({}, ("contact_date", "contact_time")),
        # Neither is blank, so one is malformed: both are named.
        (
            {"contact_date": "2026-13-40", "contact_time": "10:00"},
            ("contact_date", "contact_time"),
        ),
    ],
)
def test_incomplete_contact_names_the_missing_field(submitted, fields):
    """An incomplete contact attempt marks whichever of date and time is
    missing, and its summary links to the first of them."""
    refusal = FollowupRefusal("contact_incomplete")
    assert refusal_fields(refusal, submitted) == fields
    assert _refusal_error(refusal, submitted)["field_id"] == FIELD_IDS[fields[0]]


def render_refused(error):
    """The request page rendered as a refusal of ``error`` re-renders it."""
    item = dict(
        id=str(uuid4()),
        member_name="Member",
        ministry_name="Ministry",
        action="join",
        submitted_at=WHEN,
        state_label="New",
        outcome_label="",
        open=True,
        latest=True,
    )
    form = dict(
        expected_version="1",
        state="resolved",
        outcome="",
        notes="",
        contact_channel="phone",
        contact_date="2099-01-01",
        contact_time="10:00",
        contact_notes="",
    )
    return render_to_string(
        "stewardship/ministry-followup.html",
        dict(
            campaign_id=uuid4(),
            metadata=dict(name="Campaign", source_as_of=WHEN),
            item=item,
            form=form,
            mutable=True,
            staff_states=[(key, STATES[key]) for key in STAFF_STATES],
            resolved_outcomes=[],
            channels={"phone": "Phone"},
            history=[],
            errors=[error] if error else [],
            field_error=error,
            future_message=REFUSALS["contact_future"][0],
        ),
    )


@pytest.mark.parametrize("code", sorted(REFUSALS))
def test_refused_fields_are_marked_at_the_field(code):
    """Each refused field is aria-invalid and described by the message shown
    beside it (a date and time share one message after the time); the
    summary links to the first. Fields the refusal does not concern, and
    every field on an unrefused page, are left alone."""
    details = {
        "outcome_kind": {"outcome": "joined", "action": "join"},
        "contact_time": {"message": "“25:00” has no hour 25: hours run from 0 to 23."},
    }.get(code, {})
    error = _refusal_error(FollowupRefusal(code, **details), {})
    page = render_refused(error)
    message = re.escape(str(error["message"]).replace("'", "&#x27;"))
    for name, element in FIELD_IDS.items():
        tag = re.search(rf'<\w+ id="{element}"[^>]*>', page)[0]
        if name in error["fields"]:
            contact = name.startswith("contact")
            target = "contact-error" if contact else f"{element}-error"
            # The contact fields keep their zone note (#558) before the error.
            described = f"contact-zone-help {target}" if contact else target
            assert f'aria-invalid="true" aria-describedby="{described}"' in tag
            assert re.search(
                rf'<ul class="errorlist" id="{target}"[^>]*><li>{message}</li></ul>',
                page,
            )
        else:
            assert "aria-invalid" not in tag
    assert f'<a href="#{error["field_id"]}">' in page
    # One message shows, even when it concerns both the date and the time;
    # the contact message element is always there, hidden when unused, for
    # the page's live check of the contact time to fill.
    shown = re.findall(r'<ul class="errorlist"(?![^>]*\bhidden\b)[^>]*>', page)
    assert len(shown) == 1
    assert 'data-not-future-message="The contact attempt&#x27;s date' in page
    clean = render_refused({})
    assert "aria-invalid" not in clean
    assert '<ul class="errorlist" id="contact-error" hidden><li></li></ul>' in clean
    assert len(re.findall(r'<ul class="errorlist"', clean)) == 1
