"""Civil schedule inputs, immutable mail identities and combined date validation."""

import json
from datetime import UTC, datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from django.http import QueryDict

from parishkit.stewardship.accounts.schedule_forms import (
    BROWSER_TIME_HELP,
    ZONE_MISSING,
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
        # A ticked checkbox posts "on" (the daily digest's comparison, #1018).
        if values.get("comparison"):
            data[f"schedules-{index}-comparison"] = "on"
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
        window_data(owner, end_date="2054-10-20"),
        previous=owner["values"],
        editable=True,
    )
    assert form.is_valid(), form.errors
    assert form.values() == owner["values"] | {"end_date": "2054-10-20"}
    invalid = ScheduleWindow(
        window_data(owner, end_date="2054-09-20"),
        previous=owner["values"],
        editable=True,
    )
    assert not invalid.is_valid()
    locked = ScheduleWindow({}, previous=owner["values"], editable=False)
    assert locked.is_valid() and locked.values() == owner["values"]


def test_a_proposed_date_carries_the_saved_one_for_the_change_gate():
    """Proposed dates show as values; the saved dates stay beside them (#750)."""
    owner = campaign()
    form = ScheduleWindow(
        previous=owner["values"],
        editable=True,
        proposed={"start_date": "2054-09-30", "end_date": "2054-10-20"},
    )
    html = str(form["end_date"])
    assert 'value="2054-10-20"' in html
    assert f'data-saved-value="{owner["values"]["end_date"]}"' in html
    assert f'data-saved-value="{owner["values"]["start_date"]}"' in str(
        form["start_date"]
    )
    assert "data-saved-value" not in str(form["timezone"])
    # Each form has its own widgets: an unproposed form carries none.
    plain = ScheduleWindow(previous=owner["values"], editable=True)
    assert "data-saved-value" not in str(plain["end_date"])


def test_schedule_window_financial_overlap_requires_explicit_acknowledgement():
    """A draft date extension cannot silently move into its financial period."""
    owner = campaign(modules=["financial"], financial=financial())
    data = window_data(owner, end_date="2055-01-20")
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
            "schedules-1-date": "2054-10-05",
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


@pytest.mark.parametrize(
    ("typed", "stored"),
    [("9pm", "21:00:00"), ("0930", "09:30:00"), ("noon", "12:00:00")],
)
def test_send_times_are_typed_in_any_common_form(typed, stored):
    """The send time takes the shared time entry (#631); storage is unchanged."""
    owner = campaign()
    old = schedule(owner["id"])
    template = content(owner["id"], kind="email", slot="reminder", subject="Later")
    data = data_for([old]) | {
        "schedules-1-kind": "reminder",
        "schedules-1-date": "2054-10-05",
        "schedules-1-time": typed,
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
    [added] = formset.patch()
    assert added["operation"] == "add"
    assert added["values"]["time"] == stored
    # The page shows the saved time canonically, in a plain text box.
    shown = Schedules(
        prefix="schedules",
        previous=[old],
        templates=[template],
        campaign_id=owner["id"],
        campaign=owner["values"],
    ).forms[0]["time"]
    assert 'value="09:00"' in str(shown) and 'data-time-entry="time"' in str(shown)
    assert 'type="text"' in str(shown)


def test_a_saved_time_with_seconds_saves_unchanged_but_cannot_be_typed_anew():
    """Minute precision applies to new entries, not to a schedule saved before."""
    owner = campaign()
    old = schedule(owner["id"], time="09:00:37")

    def bound(time):
        """The saved row posted with ``time``."""
        return Schedules(
            data_for([old]) | {"schedules-0-time": time},
            prefix="schedules",
            previous=[old],
            templates=[],
            campaign_id=owner["id"],
            campaign=owner["values"],
        )

    unchanged = bound("09:00:37")
    assert unchanged.is_valid(), unchanged.errors
    assert unchanged.patch() == []
    assert 'data-time-kept="09:00:37"' in str(unchanged.forms[0]["time"])
    moved = bound("09:00:38")
    assert not moved.is_valid()
    assert moved.forms[0].errors["time"][0].endswith("leave the seconds out.")


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
        "schedules-1-date": "2054-10-05",
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
        {"schedules-0-date": "2054-11-01"},
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
    parameters["window-end_date"] = "2054-10-20"
    with pytest.raises(ValueError):
        schedule_action(parameters, window_fields=set())
    assert schedule_action(parameters, window_fields={"end_date"}) == "preview"
    parameters.setlist("window-end_date", ["2054-10-20", "2054-10-21"])
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
    for name in ("kind", "date", "time", "weekday", "template_version", "comparison"):
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


WINDOW = "October 1, 2054 – October 31, 2054"


@pytest.mark.parametrize(
    ("fields", "field", "message"),
    [
        # The owner's case: an initial invitation with a weekday.
        (
            {"kind": "initial", "date": "2054-10-01", "weekday": "0"},
            "weekday",
            "A weekday applies only to weekly digests — leave it at Not weekly.",
        ),
        (
            {"kind": "reminder", "date": ""},
            "date",
            f"Choose the date it is sent, within the campaign ({WINDOW}).",
        ),
        (
            {"kind": "reminder", "date": "2054-11-05"},
            "date",
            f"Choose a date within the campaign ({WINDOW}).",
        ),
        (
            {"kind": "reminder", "date": "2054-09-30"},
            "date",
            f"Choose a date within the campaign ({WINDOW}).",
        ),
        (
            {"kind": "daily_digest", "date": "2054-10-05"},
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
        "date": "2054-10-10",
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
        {"kind": "reminder", "date": "2054-10-31", "time": "23:59"},
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
            {"kind": "initial", "date": "2054-10-05"},
            "Only one Initial invitation is allowed. Delete the extra one.",
        ),
        (
            {"kind": "reminder", "date": "2054-10-01", "time": "08:00:00"},
            "Every reminder must be sent after the initial invitation. Choose a "
            "later date or time.",
        ),
        (
            {"kind": "reminder", "date": "2054-10-01", "time": "09:00:00"},
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
        saved_row(owner, templates, "reminder", date="2054-10-10"),
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
        date="2054-10-10",
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


def new_rows(owner, saved, templates, rows):
    """The saved rows plus several new rows, as "Add another schedule" posts."""
    data = data_for(saved, total=len(saved) + len(rows))
    for offset, fields in enumerate(rows):
        for name in ("kind", "date", "time", "weekday", "template_version"):
            value = fields.get(name, "")
            if name == "template_version":
                value = templates[fields["kind"]]["id"]
            data[f"schedules-{len(saved) + offset}-{name}"] = value
    return Schedules(
        data,
        prefix="schedules",
        previous=saved,
        templates=list(templates.values()),
        campaign_id=owner["id"],
        campaign=owner["values"],
    )


def test_several_new_rows_save_together_and_are_validated_together():
    """Rows added in the browser are ordinary formset rows: all or nothing."""
    owner = campaign()
    templates = emails(owner)
    saved = [saved_row(owner, templates)]
    rows = [
        {"kind": "reminder", "date": "2054-10-10", "time": "09:00:00"},
        {"kind": "reminder", "date": "2054-10-20", "time": "09:00:00"},
        {"kind": "weekly_digest", "weekday": "0", "time": "08:00:00"},
        {"kind": "daily_digest", "time": "07:00:00"},
    ]
    formset = new_rows(owner, saved, templates, rows)
    assert formset.is_valid(), formset.errors
    added = formset.patch()
    assert [change["operation"] for change in added] == ["add"] * 4
    assert [change["values"]["kind"] for change in added] == [
        row["kind"] for row in rows
    ]
    assert len({change["id"] for change in added}) == 4
    # A rule between the new rows refuses the whole submission.
    rows[1]["date"] = "2054-10-10"
    formset = new_rows(owner, saved, templates, rows)
    assert not formset.is_valid()
    assert formset.non_form_errors() == [
        "Two emails to Families cannot be sent at the same date and time. Change "
        "one of them."
    ]
    # Each new row's own mistakes are reported on that row's fields.
    rows[1] = {"kind": "weekly_digest", "time": "08:00:00"}
    formset = new_rows(owner, saved, templates, rows)
    assert not formset.is_valid()
    assert "weekday" in formset.forms[2].errors


def test_saved_schedules_are_shown_in_sending_order():
    """Rows appear by send date and time, Initial first on ties, stably (#448)."""
    owner = campaign()
    initial = schedule(owner["id"], date="2054-10-01", time="09:00:00")
    later = schedule(owner["id"], kind="reminder", date="2054-10-15", time="08:00:00")
    early = schedule(owner["id"], kind="reminder", date="2054-10-06", time="08:00:00")
    morning = schedule(owner["id"], kind="reminder", date="2054-10-06", time="07:00:00")
    saved = [later, early, initial, morning]
    expected = [initial["id"], morning["id"], early["id"], later["id"]]
    for previous in (saved, list(reversed(saved))):
        formset = Schedules(
            prefix="schedules",
            previous=previous,
            templates=[],
            campaign_id=owner["id"],
            campaign=owner["values"],
        )
        assert [form.initial["id"] for form in formset.initial_forms] == expected
    # A post in the displayed order is unchanged and saves nothing.
    data = data_for([initial, morning, early, later])
    formset = Schedules(
        data,
        prefix="schedules",
        previous=saved,
        templates=[],
        campaign_id=owner["id"],
        campaign=owner["values"],
    )
    assert formset.is_valid(), formset.errors
    assert formset.patch() == []


@pytest.mark.parametrize(("posted", "saved"), [("on", True), ("", None)])
def test_a_daily_digest_saves_the_comparison_only_when_ticked(posted, saved):
    """Ticked saves ``comparison: true``; unticked leaves it out (#1018)."""
    owner = campaign()
    templates = emails(owner)
    formset, _ = with_new_row(
        owner,
        [saved_row(owner, templates)],
        templates,
        kind="daily_digest",
        time="07:00:00",
        template_version="daily_digest",
        comparison=posted,
    )
    assert formset.is_valid(), (formset.errors, formset.non_form_errors())
    (added,) = formset.patch()
    assert added["values"].get("comparison") is saved


def test_only_a_daily_digest_may_include_the_comparison():
    """A weekly digest (or any other mail) with it ticked is told on the field."""
    owner = campaign()
    templates = emails(owner)
    formset, form = with_new_row(
        owner,
        [saved_row(owner, templates)],
        templates,
        kind="weekly_digest",
        weekday="2",
        time="07:00:00",
        template_version="weekly_digest",
        comparison="on",
    )
    assert not formset.is_valid()
    assert form.errors == {
        "comparison": [
            "Only daily Admin digests can include the comparison with past "
            "campaigns — leave it unticked."
        ]
    }


def test_a_saved_daily_digest_posted_unchanged_keeps_its_comparison():
    """Turning it on is a change; posting the saved row back is not."""
    owner = campaign()
    templates = emails(owner)
    initial = saved_row(owner, templates)
    digest = saved_row(
        owner, templates, kind="daily_digest", date=None, comparison=True
    )
    data = data_for([initial, digest])
    formset = Schedules(
        data,
        prefix="schedules",
        previous=[initial, digest],
        templates=list(templates.values()),
        campaign_id=owner["id"],
        campaign=owner["values"],
    )
    assert formset.is_valid(), (formset.errors, formset.non_form_errors())
    assert formset.patch() == []


# The first-campaign step's send times are the browser's (#558). The
# fixture campaign is in New York, October 1-31, 2054.
BROWSER_NOW = datetime(2054, 9, 1, 12, tzinfo=UTC)


def browser_schedules(data, owner, previous, templates=(), *, zone):
    """A first-campaign formset that takes times typed in ``zone``."""
    return Schedules(
        None if data is None else data | {"zone": zone},
        prefix="schedules",
        previous=previous,
        templates=list(templates),
        campaign_id=owner["id"],
        campaign=owner["values"],
        browser_times=True,
        zone="" if data is None else zone,
        now=BROWSER_NOW,
    )


@pytest.mark.parametrize(
    ("zone", "day", "typed", "stored_day", "stored_time"),
    [
        # Three hours behind New York.
        ("America/Los_Angeles", "2054-10-05", "6:00 AM", "2054-10-05", "09:00:00"),
        # East of UTC: Monday 1:00 AM in Auckland is Sunday in New York.
        ("Pacific/Auckland", "2054-10-05", "01:00", "2054-10-04", "08:00:00"),
        # London leaves summer time on October 25, a week before New York:
        # the same 2:00 PM is 9:00 AM there before the change, 10:00 after.
        ("Europe/London", "2054-10-24", "14:00", "2054-10-24", "09:00:00"),
        ("Europe/London", "2054-10-26", "14:00", "2054-10-26", "10:00:00"),
    ],
)
def test_first_campaign_send_times_are_typed_in_the_browser_zone(
    zone, day, typed, stored_day, stored_time
):
    """A typed date and time are converted to the campaign-local values kept."""
    owner = campaign()
    old = schedule(owner["id"])
    template = content(owner["id"], kind="email", slot="reminder", subject="Later")
    shown = data_for([old]) | {
        "schedules-0-date": "2054-10-01",
        "schedules-0-time": "09:00",
    }
    # The saved invitation as this browser shows it (09:00 New York).
    moment = datetime(2054, 10, 1, 13, tzinfo=UTC).astimezone(ZoneInfo(zone))
    shown |= {
        "schedules-0-date": moment.date().isoformat(),
        "schedules-0-time": moment.strftime("%H:%M"),
        "schedules-1-kind": "reminder",
        "schedules-1-date": day,
        "schedules-1-time": typed,
        "schedules-1-template_version": template["id"],
    }
    formset = browser_schedules(shown, owner, [old], [template], zone=zone)
    assert formset.is_valid(), (formset.errors, formset.non_form_errors())
    # The saved invitation, posted as shown, is unchanged.
    [added] = formset.patch()
    assert added["operation"] == "add"
    assert (added["values"]["date"], added["values"]["time"]) == (
        stored_day,
        stored_time,
    )


def test_a_weekly_digest_weekday_follows_the_browser_zone():
    """Monday 1:00 AM in Auckland is a Sunday digest in New York.

    A digest converts at its next send from now (September 7, 2054), when
    Auckland is still on standard time (UTC+12): Sunday 9:00 AM in New York.
    """
    owner = campaign()
    template = content(owner["id"], kind="email", slot="weekly_digest", subject="Wk")
    data = data_for([]) | {
        "schedules-0-kind": "weekly_digest",
        "schedules-0-time": "01:00",
        "schedules-0-weekday": "0",
        "schedules-0-template_version": template["id"],
    }
    formset = browser_schedules(data, owner, [], [template], zone="Pacific/Auckland")
    assert formset.is_valid(), (formset.errors, formset.non_form_errors())
    [added] = formset.patch()
    assert (added["values"]["weekday"], added["values"]["time"]) == (6, "09:00:00")


def test_an_unbound_saved_row_carries_its_moment_and_browser_help():
    """The page script fills a saved row from its UTC moment (#558)."""
    owner = campaign()
    old = schedule(owner["id"])
    form = browser_schedules(None, owner, [old], zone="").forms[0]
    assert form.schedule_moment == "2054-10-01T13:00:00+00:00"
    assert form.fields["time"].help_text == BROWSER_TIME_HELP
    # Pages that keep campaign times draw no moment.
    plain = Schedules(
        prefix="schedules",
        previous=[old],
        templates=[],
        campaign_id=owner["id"],
        campaign=owner["values"],
    ).forms[0]
    assert plain.schedule_moment is None
    assert plain.fields["time"].help_text != BROWSER_TIME_HELP
    with pytest.raises(ValueError, match="current instant"):
        Schedules(
            prefix="schedules",
            previous=[],
            templates=[],
            campaign_id=owner["id"],
            campaign=owner["values"],
            browser_times=True,
        )


def test_a_saved_time_with_seconds_is_kept_in_the_browser_zone():
    """A legacy 9:00:37 shown as 6:00:37 in Los Angeles saves unchanged (#631)."""
    owner = campaign()
    old = schedule(owner["id"], time="09:00:37")
    data = data_for([old]) | {"schedules-0-time": "06:00:37"}
    formset = browser_schedules(data, owner, [old], zone="America/Los_Angeles")
    assert formset.is_valid(), (formset.errors, formset.non_form_errors())
    assert formset.patch() == []
    assert 'data-time-kept="06:00:37"' in str(formset.forms[0]["time"])


@pytest.mark.parametrize("zone", ["", "Etc/Unknown", "../etc/passwd"])
def test_a_missing_or_unknown_browser_zone_is_refused(zone):
    """Without a usable zone the typed times cannot be read; nothing is guessed."""
    owner = campaign()
    old = schedule(owner["id"])
    formset = browser_schedules(data_for([old]), owner, [old], zone=zone)
    assert not formset.is_valid()
    assert formset.non_form_errors() == [str(ZONE_MISSING)]


def test_a_browser_time_past_the_campaign_end_is_outside_it():
    """10:00 PM on October 31 in Los Angeles is November 1 in New York."""
    owner = campaign()
    old = schedule(owner["id"])
    template = content(owner["id"], kind="email", slot="reminder", subject="Later")
    data = data_for([old]) | {
        "schedules-0-time": "06:00",
        "schedules-1-kind": "reminder",
        "schedules-1-date": "2054-10-31",
        "schedules-1-time": "10:00 PM",
        "schedules-1-template_version": template["id"],
    }
    formset = browser_schedules(
        data, owner, [old], [template], zone="America/Los_Angeles"
    )
    assert not formset.is_valid()
    assert (
        formset.forms[1]
        .errors["date"][0]
        .startswith("Choose a date within the campaign")
    )


def test_a_saved_send_in_a_repeated_hour_saves_unchanged():
    """London's 1:30 AM on October 25, 2054 happens twice; the second is kept.

    9:30 PM in New York on October 24 is 01:30 UTC, the second 1:30 AM in
    London (after its clocks fall back). Read again, 1:30 AM would be the
    first (00:30 UTC), so only the "posted as shown" check keeps it.
    """
    owner = campaign()
    template = content(owner["id"], kind="email", slot="reminder", subject="Later")
    initial = schedule(owner["id"])
    late = schedule(
        owner["id"],
        kind="reminder",
        date="2054-10-24",
        time="21:30:00",
        template_version=template["id"],
        subject="Later",
    )
    rows = [initial, late]
    unbound = browser_schedules(None, owner, rows, [template], zone="")
    assert unbound.forms[1].schedule_moment == "2054-10-25T01:30:00+00:00"
    data = data_for(rows) | {
        "schedules-0-time": "14:00",
        "schedules-1-date": "2054-10-25",
        "schedules-1-time": "01:30",
    }
    formset = browser_schedules(data, owner, rows, [template], zone="Europe/London")
    assert formset.is_valid(), (formset.errors, formset.non_form_errors())
    assert formset.patch() == []


def test_a_kept_legacy_time_follows_the_posted_row_id():
    """Seconds are kept for the saved row the post names, whatever its position."""
    owner = campaign()
    old = schedule(owner["id"], time="09:00:37")
    other = schedule(owner["id"], kind="reminder", date="2054-10-05")
    rows = [old, other]
    # Swap the two rows' IDs: the kept seconds follow each ID, not the index.
    swapped = data_for(rows) | {
        "schedules-0-id": other["id"],
        "schedules-1-id": old["id"],
    }
    formset = browser_schedules(swapped, owner, rows, zone="America/Los_Angeles")
    assert 'data-time-kept="06:00:37"' in str(formset.forms[1]["time"])
    assert "data-time-kept" not in str(formset.forms[0]["time"])
