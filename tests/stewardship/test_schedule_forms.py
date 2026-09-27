"""Civil schedule inputs, immutable mail identities and combined date validation."""

import json
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
        owner["id"], kind="email", slot="initial", subject="New invitation"
    )
    data = data_for([old]) | {
        "schedules-0-DELETE": "on",
        "schedules-1-kind": "initial",
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
    assert patch[1]["values"]["subject"] == "New invitation"
    assert patch[1]["values"]["time"] == "09:00:00"


@pytest.mark.parametrize(
    "changes",
    [
        {"schedules-INITIAL_FORMS": "0"},
        {"schedules-0-id": str(uuid4())},
        {"schedules-0-id": ""},
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


def emails(owner):
    """One saved email of every schedulable mail type, keyed by that type."""
    return {
        kind: content(owner["id"], kind="email", slot=kind)
        for kind in ("initial", "reminder", "daily_digest", "weekly_digest")
    }


def saved_row(owner, templates, kind="initial", **overrides):
    """A saved schedule already sending the current email of its mail type."""
    template = templates[kind]
    return schedule(
        owner["id"],
        kind=kind,
        template_version=template["id"],
        subject=template["values"]["subject"],
        **overrides,
    )


def with_new_row(owner, saved, templates, **fields):
    """The saved rows plus one new row with exactly the given fields posted."""
    data = data_for(saved)
    index = len(saved)
    for name in ("kind", "date", "time", "weekday", "template_version"):
        value = fields.get(name, "")
        if name == "template_version" and value in templates:
            value = templates[value]["id"]
        data[f"schedules-{index}-{name}"] = value
    formset = Schedules(
        data,
        prefix="schedules",
        previous=saved,
        templates=list(templates.values()),
        campaign_id=owner["id"],
        campaign=owner["values"],
    )
    return formset, formset.forms[index]


WINDOW = "October 1, 2026 – October 31, 2026"


@pytest.mark.parametrize(
    ("fields", "field", "message"),
    [
        # The owner's case: an initial invitation with a weekday.
        (
            {"kind": "initial", "date": "2026-10-01", "weekday": "0"},
            "weekday",
            "A weekday applies only to weekly digests — leave it at Not weekly.",
        ),
        (
            {"kind": "reminder", "date": ""},
            "date",
            f"Choose the date it is sent, within the campaign ({WINDOW}).",
        ),
        (
            {"kind": "reminder", "date": "2026-11-05"},
            "date",
            f"Choose a date within the campaign ({WINDOW}).",
        ),
        (
            {"kind": "reminder", "date": "2026-09-30"},
            "date",
            f"Choose a date within the campaign ({WINDOW}).",
        ),
        (
            {"kind": "daily_digest", "date": "2026-10-05"},
            "date",
            "A date applies only to initial invitations and reminders — leave it "
            "empty. Digests are sent throughout the campaign.",
        ),
        (
            {"kind": "weekly_digest", "date": "", "weekday": ""},
            "weekday",
            "Choose the day of the week the digest is sent.",
        ),
        (
            {"kind": "daily_digest", "date": "", "weekday": "3"},
            "weekday",
            "A weekday applies only to weekly digests — leave it at Not weekly.",
        ),
        (
            {"kind": "reminder", "time": ""},
            "time",
            "Enter the time of day it is sent, for example 9:00 AM.",
        ),
        (
            {"kind": "reminder", "template_version": ""},
            "template_version",
            "Choose the email to send.",
        ),
        (
            {"kind": "reminder", "template_version": "initial"},
            "template_version",
            "Choose an email of this mail type (Reminder).",
        ),
        ({"kind": ""}, "kind", "Choose a mail type."),
    ],
)
def test_each_inapplicable_or_missing_value_is_reported_on_its_field(
    fields, field, message
):
    """Say which field is wrong and what to do; no combined collection error."""
    owner = campaign()
    templates = emails(owner)
    saved = [] if fields.get("kind") == "initial" else [saved_row(owner, templates)]
    row = {
        "date": "2026-10-10",
        "time": "10:00:00",
        "template_version": fields.get("kind") or "reminder",
    } | fields
    formset, form = with_new_row(owner, saved, templates, **row)
    assert not formset.is_valid()
    assert form.errors == {field: [message]}
    assert formset.non_form_errors() == []


@pytest.mark.parametrize(
    "fields",
    [
        {"kind": "reminder", "date": "2026-10-31", "time": "23:59:59"},
        {"kind": "daily_digest", "time": "00:15:00"},
        {"kind": "weekly_digest", "weekday": "6", "time": "08:00:00"},
    ],
)
def test_each_mail_type_accepts_exactly_its_own_fields(fields):
    """The campaign's last day is inclusive; digests take no date."""
    owner = campaign()
    templates = emails(owner)
    saved = [saved_row(owner, templates)]
    formset, _ = with_new_row(
        owner, saved, templates, template_version=fields["kind"], **fields
    )
    assert formset.is_valid(), (formset.errors, formset.non_form_errors())
    (added,) = formset.patch()
    assert added["values"]["kind"] == fields["kind"]


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        (
            {"kind": "initial", "date": "2026-10-05"},
            "Only one Initial invitation is allowed. Delete the extra one.",
        ),
        (
            {"kind": "reminder", "date": "2026-10-01", "time": "08:00:00"},
            "Every reminder must be sent after the initial invitation. Choose a "
            "later date or time.",
        ),
        (
            {"kind": "reminder", "date": "2026-10-01", "time": "09:00:00"},
            "Every reminder must be sent after the initial invitation. Choose a "
            "later date or time.",
        ),
        (
            {"kind": "daily_digest", "date": "", "time": "07:00:00"},
            "Only one Daily Admin digest and one Weekly Admin digest are allowed. "
            "Delete the extra one.",
        ),
    ],
)
def test_problems_between_schedules_are_collection_errors(fields, message):
    """Only rules that involve more than one row stay non-field errors."""
    owner = campaign()
    templates = emails(owner)
    saved = [
        saved_row(owner, templates),
        saved_row(owner, templates, "daily_digest", date=None),
    ]
    formset, form = with_new_row(
        owner,
        saved,
        templates,
        **({"time": "10:00:00", "template_version": fields["kind"]} | fields),
    )
    assert not formset.is_valid()
    assert form.errors == {}
    assert message in formset.non_form_errors()


def test_reminders_need_an_initial_invitation_and_distinct_times():
    """Deleting the only invitation strands reminders; mail times never repeat."""
    owner = campaign()
    templates = emails(owner)
    saved = [
        saved_row(owner, templates),
        saved_row(owner, templates, "reminder", date="2026-10-10"),
    ]
    data = data_for(saved) | {"schedules-0-DELETE": "on"}
    formset = Schedules(
        data,
        prefix="schedules",
        previous=saved,
        templates=list(templates.values()),
        campaign_id=owner["id"],
        campaign=owner["values"],
    )
    assert not formset.is_valid()
    assert formset.non_form_errors() == [
        "Reminders need an initial invitation. Add one Initial invitation schedule."
    ]
    formset, _ = with_new_row(
        owner,
        saved,
        templates,
        kind="reminder",
        date="2026-10-10",
        time="09:00:00",
        template_version="reminder",
    )
    assert not formset.is_valid()
    assert formset.non_form_errors() == [
        "Two emails to Families cannot be sent at the same date and time. Change "
        "one of them."
    ]


def test_saved_mail_type_is_fixed_and_offers_only_its_own_emails():
    """A saved row cannot be retyped; a new row offers every type, each tagged."""
    owner = campaign()
    templates = emails(owner)
    row = saved_row(owner, templates)
    formset = Schedules(
        data_for([row]) | {"schedules-0-kind": "reminder"},
        prefix="schedules",
        previous=[row],
        templates=list(templates.values()),
        campaign_id=owner["id"],
        campaign=owner["values"],
    )
    assert formset.is_valid(), formset.errors
    assert formset.patch() == []
    saved, blank = formset.forms
    assert saved.fields["kind"].disabled and not blank.fields["kind"].disabled
    assert [key for key, _ in saved.fields["template_version"].choices] == [
        "",
        templates["initial"]["id"],
    ]
    html = str(blank["template_version"])
    for kind, template in templates.items():
        assert f'value="{template["id"]}" data-kind="{kind}"' in html
    # Only saved rows can be deleted; a blank new row has nothing to delete.
    assert "DELETE" in saved.fields and "DELETE" not in blank.fields
    assert formset.window_text == WINDOW
    assert formset.timezone == "America/New_York"
    assert json.loads(blank.field_rules)["weekly_digest"] == [
        "weekday",
        "time",
        "template_version",
    ]
