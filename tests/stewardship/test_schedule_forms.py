"""Civil schedule inputs, immutable mail identities and combined date validation."""

from uuid import uuid4

import pytest
from django.http import QueryDict

from parishkit.stewardship.accounts.schedule_forms import (
    Schedules,
    ScheduleWindow,
    schedule_action,
)

from .campaign_factory import campaign, financial, schedule
from .content_factory import content


def data_for(rows, *, total=None):
    """Model ordinary posted form fields, including the explicit blank last row."""
    data = {
        "action": "preview",
        "schedules-TOTAL_FORMS": str(len(rows) + 1 if total is None else total),
        "schedules-INITIAL_FORMS": str(len(rows)),
    }
    for index, row in enumerate(rows):
        values = row["values"] | {"id": row["id"]}
        for name in ("id", "kind", "date", "time", "weekday", "template_version"):
            value = values.get(name)
            data[f"schedules-{index}-{name}"] = "" if value is None else str(value)
    return data


def window_data(owner, **changes):
    """Only the draft window and optional overlap acknowledgment are structural."""
    return {
        name: owner["values"][name] for name in ("timezone", "start_date", "end_date")
    } | changes


def test_schedule_window_requires_valid_whole_campaign_and_preserves_other_fields():
    """Dates change without recreating IDs, modules or the Parish default timezone."""
    owner = campaign()
    form = ScheduleWindow(
        window_data(owner, end_date="2026-10-20"),
        previous=owner["values"],
        editable=True,
    )
    assert form.is_valid(), form.errors
    assert form.values() == owner["values"] | {"end_date": "2026-10-20"}
    invalid = ScheduleWindow(
        window_data(owner, end_date="2026-09-20"),
        previous=owner["values"],
        editable=True,
    )
    assert not invalid.is_valid()
    locked = ScheduleWindow({}, previous=owner["values"], editable=False)
    assert locked.is_valid() and locked.values() == owner["values"]


def test_schedule_window_financial_overlap_requires_explicit_acknowledgement():
    """A draft date extension cannot silently move into its financial period."""
    owner = campaign(modules=["financial"], financial=financial())
    data = window_data(owner, end_date="2027-01-20")
    form = ScheduleWindow(data, previous=owner["values"], editable=True)
    assert not form.is_valid()
    form = ScheduleWindow(
        data | {"overlap_confirmed": "on"}, previous=owner["values"], editable=True
    )
    assert form.is_valid() and form.values()["financial"]["overlap_confirmed"]


def test_existing_legacy_template_is_retained_but_not_offered_to_new_schedule():
    """Unresolved legacy references remain visible, not readiness evidence."""
    owner = campaign()
    row = schedule(owner["id"])
    data = data_for([row])
    formset = Schedules(
        data,
        prefix="schedules",
        previous=[row],
        templates=[],
        campaign_id=owner["id"],
        campaign=owner["values"],
    )
    assert formset.is_valid(), formset.errors
    assert formset.patch() == []
    data.update(
        {
            "schedules-1-kind": "reminder",
            "schedules-1-date": "2026-10-05",
            "schedules-1-time": "09:00:00",
            "schedules-1-template_version": row["values"]["template_version"],
        }
    )
    invalid = Schedules(
        data,
        prefix="schedules",
        previous=[row],
        templates=[],
        campaign_id=owner["id"],
        campaign=owner["values"],
    )
    assert not invalid.is_valid()


def test_schedule_add_replace_and_explicit_remove():
    """Allocate new IDs server-side; subjects follow selected immutable content."""
    owner = campaign()
    old = schedule(owner["id"])
    template = content(
        owner["id"], kind="email", slot="reminder", subject="New reminder"
    )
    data = data_for([old]) | {
        "schedules-0-DELETE": "on",
        "schedules-1-kind": "reminder",
        "schedules-1-date": "2026-10-05",
        "schedules-1-time": "09:00",
        "schedules-1-template_version": template["id"],
    }
    formset = Schedules(
        data,
        prefix="schedules",
        previous=[old],
        templates=[template],
        campaign_id=owner["id"],
        campaign=owner["values"],
    )
    assert formset.is_valid(), formset.errors
    patch = formset.patch()
    assert patch[0] == {"operation": "remove", "section": "schedules", "id": old["id"]}
    assert patch[1]["operation"] == "add" and patch[1]["id"] != old["id"]
    assert patch[1]["values"]["subject"] == "New reminder"
    assert patch[1]["values"]["time"] == "09:00:00"


@pytest.mark.parametrize(
    "changes",
    [
        {"schedules-INITIAL_FORMS": "0"},
        {"schedules-0-id": str(uuid4())},
        {"schedules-0-id": ""},
        {"schedules-0-kind": "reminder"},
        {"schedules-0-date": "2026-11-01"},
        {"schedules-0-weekday": "0"},
        {"schedules-0-time": "invalid"},
        {"schedules-0-time": "09:00:00.5"},
        {"schedules-0-time": "09:00:00+04:00"},
    ],
)
def test_schedule_invalid_identity_or_civil_values(changes):
    """Hidden management data cannot rebind or silently omit a saved schedule."""
    owner = campaign()
    row = schedule(owner["id"])
    formset = Schedules(
        data_for([row]) | changes,
        prefix="schedules",
        previous=[row],
        templates=[],
        campaign_id=owner["id"],
        campaign=owner["values"],
    )
    assert not formset.is_valid()
    with pytest.raises(ValueError):
        formset.patch()


@pytest.mark.parametrize("wrong", ["kind", "campaign"])
def test_schedule_template_scope_is_not_a_label_or_browser_claim(wrong):
    """Even mistakenly supplied choices cannot cross campaign or message purpose."""
    owner = campaign()
    row = schedule(owner["id"])
    template = content(
        owner["id"] if wrong == "kind" else str(uuid4()),
        kind="email",
        slot="reminder" if wrong == "kind" else "initial",
    )
    formset = Schedules(
        data_for([row]) | {"schedules-0-template_version": template["id"]},
        prefix="schedules",
        previous=[row],
        templates=[template],
        campaign_id=owner["id"],
        campaign=owner["values"],
    )
    assert not formset.is_valid()


@pytest.mark.parametrize("count", ["-1", "000", "102", "100000", "１", ""])
def test_bounded_schedule_management_parser(count):
    """Reject oversized counts before allocating forms; no Unicode/numeric coercion."""
    parameters = QueryDict(mutable=True)
    parameters.update({"action": "preview", "schedules-TOTAL_FORMS": count})
    with pytest.raises(ValueError):
        schedule_action(parameters, window_fields=set())


def test_schedule_parser_rejects_duplicate_and_unoffered_fields():
    """Locked window fields and repeated scalar values are not silently ignored."""
    parameters = QueryDict(mutable=True)
    parameters.update(data_for([]))
    assert schedule_action(parameters, window_fields=set()) == "preview"
    parameters["window-end_date"] = "2026-10-20"
    with pytest.raises(ValueError):
        schedule_action(parameters, window_fields=set())
    assert schedule_action(parameters, window_fields={"end_date"}) == "preview"
    parameters.setlist("window-end_date", ["2026-10-20", "2026-10-21"])
    with pytest.raises(ValueError):
        schedule_action(parameters, window_fields={"end_date"})
