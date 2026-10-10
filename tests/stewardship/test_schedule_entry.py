"""New, Edit and Delete scheduled email (#878): forms, mapping and table rows.

The views' database paths are in database/test_schedule_entry_postgresql.py;
these check, without a database, how a page's post becomes the change
document the schedule review takes, how the review's errors come back to the
page, and what the list shows and offers per row.
"""

from datetime import UTC, date, datetime, time
from uuid import uuid4

import pytest
from django import template
from django.http import QueryDict
from django.template import Context, Template

from parishkit.stewardship.accounts import schedule_local
from parishkit.stewardship.accounts.schedule_entry_views import (
    SCHEDULE_LIMIT,
    RepeatRule,
    _carry_errors,
    _check_repeat,
    _chosen,
    _days,
    _entries,
    _fields,
    _targets,
    entry_form,
    entry_times,
    mailings,
)
from parishkit.stewardship.accounts.schedule_forms import (
    Schedules,
    ScheduleWindow,
    schedule_order,
    window_text,
)
from parishkit.stewardship.accounts.schedule_table import SORTING, schedule_rows
from parishkit.stewardship.web.dates import UnknownZone
from parishkit.stewardship.web.refusals import UserFacingError
from parishkit.stewardship.web.tables import whole_table

from .campaign_factory import campaign, schedule
from .content_factory import content
from .test_schedule_forms import data_for

OWNER = campaign()
VALUES = OWNER["values"]
NOW = datetime(2054, 10, 5, 12, tzinfo=UTC)
# The campaign's zone, and a browser's three hours behind it.
ZONE = VALUES["timezone"]
LOS_ANGELES = "America/Los_Angeles"


def emails():
    """One saved email of each schedulable type."""
    return [
        content(OWNER["id"], kind="email", slot=kind, subject=f"{kind} mail")
        for kind in ("initial", "reminder", "daily_digest", "weekly_digest")
    ]


def saved(templates):
    """A sent invitation, two reminders and a weekly digest, in sending order."""
    by_kind = {row["values"]["slot"]: row["id"] for row in templates}
    rows = [
        schedule(OWNER["id"], template_version=by_kind["initial"]),
        schedule(
            OWNER["id"],
            kind="reminder",
            date="2054-10-10",
            template_version=by_kind["reminder"],
        ),
        schedule(
            OWNER["id"],
            kind="reminder",
            date="2054-10-20",
            template_version=by_kind["reminder"],
        ),
        schedule(
            OWNER["id"],
            kind="weekly_digest",
            date=None,
            weekday=0,
            template_version=by_kind["weekly_digest"],
        ),
    ]
    return sorted(rows, key=schedule_order)


def posted(**fields):
    """A page's POST body as the browser sends it (lists for repeats)."""
    data = QueryDict(mutable=True)
    for name, value in fields.items():
        data.setlist(name, value if isinstance(value, list) else [value])
    return data


def test_rows_offer_edit_only_until_a_schedule_has_run():
    """A sent invitation has no edit page; upcoming reminders and digests do."""
    templates = emails()
    rows = saved(templates)
    summary = {rows[0]["id"]: {"delivered": 5}}
    described = schedule_rows(rows, VALUES, summary, NOW)
    assert [row.edit_url for row in described][0] is None
    assert described[1].edit_url == f"/admin/campaign/schedules/{rows[1]['id']}/"
    assert described[3].edit_url  # a digest repeats and stays editable


def test_rows_show_each_email_by_its_readable_name():
    """The name comes from the email; an unknown email keeps the subject."""
    templates = emails()
    rows = saved(templates)
    names = {rows[1]["values"]["template_version"]: "Please respond (1a2b3c4d)"}
    described = schedule_rows(rows, VALUES, {}, NOW, names)
    assert described[1].email == "Please respond (1a2b3c4d)"
    assert described[0].email == rows[0]["values"]["subject"]


def test_when_sorts_sending_order_or_its_reverse():
    """The default is sending order; ``-when`` reverses it, digests first."""
    described = schedule_rows(saved(emails()), VALUES, {}, NOW)
    labels = [
        row.label for row in whole_table(described, sorting=SORTING, sort="when").rows
    ]
    assert labels == [
        "Initial invitation",
        "Reminder 1",
        "Reminder 2",
        "Weekly Admin digest",
    ]
    backwards = whole_table(described, sorting=SORTING, sort="-when").rows
    assert [row.label for row in backwards] == list(reversed(labels))
    with pytest.raises(ValueError):
        whole_table(described, sorting=SORTING, sort="subject")


def test_window_text_names_the_campaign_dates():
    """The one line of dates and the guide read the same words."""
    assert window_text(VALUES) == "October 1, 2054 – October 31, 2054"


def test_entry_form_fixes_a_saved_mail_type_and_never_reads_a_posted_id():
    """Edit keeps the saved type; the address names the schedule."""
    templates = emails()
    rows = saved(templates)
    form = entry_form(None, templates, rows, rows[1])
    assert "id" not in form.fields
    assert form.fields["kind"].disabled
    assert form.schedule_label == "Reminder 1"
    assert form["date"].value() == "2054-10-10"
    blank = entry_form(None, templates, rows)
    assert not blank.fields["kind"].disabled and blank.schedule_label is None
    # The page's help names no campaign zone: times are this computer's.
    assert "this computer's time zone" in str(form.fields["time"].help_text)
    assert "campaign" not in str(form.fields["time"].help_text)


def test_entry_times_carry_moments_for_the_browser_to_show():
    """Saved mailings, the campaign's bounds and an Edit page's own moment."""
    rows = saved(emails())
    times = entry_times(VALUES, rows, rows[1], NOW, bound=False)
    # Reminder 1 sends at 9:00 AM New York time on October 10.
    assert times["local_from"] == "2054-10-10T13:00:00+00:00"
    assert times["window_start"] == "2054-10-01T04:00:00+00:00"
    assert times["window_end"] == "2054-11-01T04:00:00+00:00"
    assert times["room"] == SCHEDULE_LIMIT - len(rows) and times["limit"] == 100
    # A refused page shows what was posted, already in the browser's zone.
    assert entry_times(VALUES, rows, rows[1], NOW, bound=True)["local_from"] == ""
    assert entry_times(VALUES, rows, None, NOW, bound=False)["local_from"] == ""


def test_fields_allow_repeat_only_on_new():
    """Edit posts the schedule's fields; New adds the repeat rule."""
    assert "zone" in _fields(True) and "zone" in _fields(False)
    assert "repeat-dates" in _fields(True)
    assert not any(name.startswith("repeat-") for name in _fields(False))
    assert "schedule-id" not in _fields(True)


def valid_entry(templates, rows, schedule_row=None, **fields):
    """A bound, valid page form with ``fields`` posted."""
    form = entry_form(
        posted(**{f"schedule-{name}": value for name, value in fields.items()}),
        templates,
        rows,
        schedule_row,
    )
    assert form.is_valid(), form.errors
    return form


def entries(entry, repeat, schedule_row, zone=ZONE):
    """``_entries`` for a page posted from a browser in ``zone``."""
    return _entries(entry, repeat, schedule_row, zone=zone, campaign_zone=ZONE, now=NOW)


def test_entries_are_one_schedule_or_one_reminder_per_repeat_date():
    """New posts its type; Edit its ID; Repeat one reminder per date."""
    templates = emails()
    rows = saved(templates)
    reminder = templates[1]["id"]
    entry = valid_entry(
        templates,
        rows,
        kind="reminder",
        date="2054-10-12",
        time="9am",
        template_version=reminder,
    )
    assert entries(entry, None, None) == [
        {
            "kind": "reminder",
            "date": "2054-10-12",
            "time": "09:00:00",
            "weekday": None,
            "template_version": reminder,
        }
    ]
    change = valid_entry(
        templates,
        rows,
        rows[1],
        date="2054-10-11",
        time="10:30",
        template_version=reminder,
    )
    assert entries(change, None, rows[1]) == [
        {
            "id": rows[1]["id"],
            "date": "2054-10-11",
            "time": "10:30:00",
            "weekday": None,
            "template_version": reminder,
        }
    ]
    repeat = RepeatRule(
        posted(
            **{
                "repeat-repeat": "on",
                "repeat-dates": ["2054-10-27", "2054-10-13"],
            }
        ),
        prefix="repeat",
    )
    assert repeat.is_valid(), repeat.errors
    repeated = entries(entry, repeat, None)
    assert [item["date"] for item in repeated] == ["2054-10-13", "2054-10-27"]
    assert {item["kind"] for item in repeated} == {"reminder"}
    assert _targets(repeated, None, _days(entry, repeat)) == {
        "new0": date(2054, 10, 13),
        "new1": date(2054, 10, 27),
    }
    assert _targets(repeated[:1], None, None) == {"new0": None}
    assert _targets([], rows[1], None) == {rows[1]["id"]: None}


def test_entries_convert_the_browser_time_to_the_campaign_zone():
    """9 AM in Los Angeles is noon in New York; a late time moves the date."""
    templates = emails()
    rows = saved(templates)
    reminder = templates[1]["id"]
    entry = valid_entry(
        templates,
        rows,
        kind="reminder",
        date="2054-10-12",
        time="9am",
        template_version=reminder,
    )
    (item,) = entries(entry, None, None, LOS_ANGELES)
    assert (item["date"], item["time"]) == ("2054-10-12", "12:00:00")
    late = valid_entry(
        templates,
        rows,
        rows[1],
        date="2054-10-12",
        time="22:30",
        template_version=reminder,
    )
    (item,) = entries(late, None, rows[1], LOS_ANGELES)
    assert (item["date"], item["time"]) == ("2054-10-13", "01:30:00")
    # Each repeat date converts on its own date.
    repeat = RepeatRule(
        posted(**{"repeat-repeat": "on", "repeat-dates": ["2054-10-27"]}),
        prefix="repeat",
    )
    assert repeat.is_valid()
    (item,) = entries(entry, repeat, None, LOS_ANGELES)
    assert (item["date"], item["time"]) == ("2054-10-27", "12:00:00")
    for unknown in ("", "Mars/Olympus_Mons", "../etc/passwd"):
        with pytest.raises(UnknownZone):
            entries(entry, None, None, unknown)


def test_digests_convert_at_their_next_send():
    """A weekly digest's weekday and time move together across midnight."""
    # Monday 9:00 PM in Los Angeles is Tuesday 00:00 in New York.
    assert schedule_local.to_campaign(
        None, time(21), 0, zone=LOS_ANGELES, campaign_zone=ZONE, now=NOW
    ) == (None, time(0), 1)
    assert schedule_local.to_campaign(
        None, time(6), None, zone=LOS_ANGELES, campaign_zone=ZONE, now=NOW
    ) == (None, time(9), None)
    # And back: the saved digest's next send, shown in the browser's zone.
    digest = {"kind": "weekly_digest", "date": None, "weekday": 1, "time": "00:00:00"}
    moment = schedule_local.instant(digest, ZONE, NOW)
    assert moment == datetime(2054, 10, 6, 4, tzinfo=UTC)
    assert schedule_local.in_browser(digest, LOS_ANGELES, ZONE, NOW) == time(21)


CHICAGO = "America/Chicago"
NEW_YORK = "America/New_York"


def test_typed_times_at_daylight_saving_changes_read_first_or_forward():
    """A time typed twice-over is the first; a skipped one moves forward."""
    # 1:30 AM New York on fall-back day: the first (EDT, 05:30 UTC), which
    # is 00:30 in Chicago.
    assert schedule_local.to_campaign(
        date(2026, 11, 1),
        time(1, 30),
        None,
        zone=NEW_YORK,
        campaign_zone=CHICAGO,
        now=NOW,
    ) == (date(2026, 11, 1), time(0, 30), None)
    # 2:30 AM New York on spring-forward day is 3:30 AM EDT (07:30 UTC),
    # still 1:30 AM standard time in Chicago.
    assert schedule_local.to_campaign(
        date(2027, 3, 14),
        time(2, 30),
        None,
        zone=NEW_YORK,
        campaign_zone=CHICAGO,
        now=NOW,
    ) == (date(2027, 3, 14), time(1, 30), None)


def test_an_unchanged_edit_keeps_the_saved_values_at_fall_back():
    """Saving Edit as shown never moves a send by an hour.

    01:30 Chicago on 2026-11-01 is 06:30 UTC, which New York shows as its
    second 1:30 AM. Reading that 1:30 again would take the first (05:30
    UTC, 00:30 Chicago); the page's values match what it showed, so the
    saved values are kept. A changed time still converts as typed.
    """
    reminder = {
        "kind": "reminder",
        "date": "2026-11-01",
        "time": "01:30:00",
        "weekday": None,
    }
    assert schedule_local.instant(reminder, CHICAGO, NOW) == datetime(
        2026, 11, 1, 6, 30, tzinfo=UTC
    )
    shown = {"zone": NEW_YORK, "campaign_zone": CHICAGO, "now": NOW}
    assert schedule_local.in_browser(reminder, NEW_YORK, CHICAGO, NOW) == time(1, 30)
    assert schedule_local.shown(reminder, date(2026, 11, 1), time(1, 30), None, **shown)
    assert schedule_local.to_campaign(
        date(2026, 11, 1), time(1, 30), None, saved=reminder, **shown
    ) == (date(2026, 11, 1), time(1, 30), None)
    # Changed date, time or weekday: converted as typed.
    for day, clock in (
        (date(2026, 11, 1), time(1, 45)),
        (date(2026, 11, 2), time(1, 30)),
    ):
        assert not schedule_local.shown(reminder, day, clock, None, **shown)
    assert schedule_local.to_campaign(
        date(2026, 11, 1), time(1, 45), None, saved=reminder, **shown
    ) == (date(2026, 11, 1), time(0, 45), None)
    # A weekly digest is compared on its weekday too.
    digest = {"kind": "weekly_digest", "date": None, "weekday": 1, "time": "00:00:00"}
    in_ny = schedule_local.in_browser(digest, NEW_YORK, CHICAGO, NOW)
    assert schedule_local.shown(digest, None, in_ny, 1, **shown)
    assert not schedule_local.shown(digest, None, in_ny, 2, **shown)
    assert not schedule_local.shown(digest, None, in_ny, None, **shown)


@pytest.mark.parametrize(
    "dates",
    [
        [f"2054-10-{day:02d}" for day in range(1, 32)],  # 31: over the limit
        ["2054-10-13", "2054-10-13"],
        ["2054-13-01"],
    ],
)
def test_repeat_refuses_too_many_repeated_or_unreadable_dates(dates):
    """At most 30 distinct calendar days."""
    repeat = RepeatRule(
        posted(**{"repeat-repeat": "on", "repeat-dates": dates}), prefix="repeat"
    )
    assert not repeat.is_valid()
    assert "dates" in repeat.errors


def test_repeat_is_for_reminders_with_at_least_one_date():
    """An invitation cannot repeat; a rule with no date that fits says so."""
    templates = emails()
    rows = saved(templates)
    invitation = valid_entry(
        templates,
        rows,
        kind="initial",
        date="2054-10-02",
        time="09:00",
        template_version=templates[0]["id"],
    )
    repeat = RepeatRule(
        posted(**{"repeat-repeat": "on", "repeat-dates": "2054-10-13"}),
        prefix="repeat",
    )
    assert repeat.is_valid()
    _check_repeat(invitation, repeat)
    assert repeat.errors["repeat"] == ["Only reminders repeat."]
    reminder = valid_entry(
        templates,
        rows,
        kind="reminder",
        time="09:00",
        template_version=templates[1]["id"],
    )
    empty = RepeatRule(posted(**{"repeat-repeat": "on"}), prefix="repeat")
    assert empty.is_valid()
    _check_repeat(reminder, empty)
    assert "no date that can be added" in empty.errors["repeat"][0]


def test_review_errors_come_back_to_the_page_field_or_its_top():
    """The changed row's errors go on its fields; other rows' name the row."""
    templates = emails()
    rows = saved(templates)
    data = data_for(rows, total=len(rows) + 1)
    # The new row (index 4): a reminder outside the campaign.
    data |= {
        "schedules-4-kind": "reminder",
        "schedules-4-date": "2054-11-15",
        "schedules-4-time": "09:00",
        "schedules-4-template_version": templates[1]["id"],
    }
    schedules = Schedules(
        data,
        prefix="schedules",
        previous=rows,
        templates=templates,
        campaign_id=OWNER["id"],
        campaign=VALUES,
    )
    window = ScheduleWindow(None, prefix="window", previous=VALUES, editable=False)
    assert not schedules.is_valid()
    identifiers = [row["id"] for row in rows] + ["new0"]
    entry = valid_entry(
        templates,
        rows,
        kind="reminder",
        date="2054-11-15",
        time="09:00",
        template_version=templates[1]["id"],
    )
    _carry_errors(entry, window, schedules, identifiers, {"new0": None})
    assert "date" in entry.errors
    # A repeat date's own date error names the date at the top instead.
    repeating = valid_entry(
        templates,
        rows,
        kind="reminder",
        time="09:00",
        template_version=templates[1]["id"],
    )
    _carry_errors(
        repeating, window, schedules, identifiers, {"new0": date(2054, 11, 15)}
    )
    assert repeating.non_field_errors()[0].startswith("November 15, 2054: ")


def test_mailings_list_saved_family_mail_as_moments():
    """The repeat rule checks against invitations and reminders only."""
    rows = saved(emails())
    assert mailings(rows, None, ZONE, NOW) == [
        {"kind": "initial", "at": "2054-10-01T13:00:00+00:00"},
        {"kind": "reminder", "at": "2054-10-10T13:00:00+00:00"},
        {"kind": "reminder", "at": "2054-10-20T13:00:00+00:00"},
    ]
    assert len(mailings(rows, rows[1], ZONE, NOW)) == 2


def test_a_delete_names_one_to_a_hundred_distinct_schedules():
    """Only the form's fields; each ID a UUID, none twice; each refusal says why."""
    first, second = (row["id"] for row in saved(emails())[1:3])
    chosen = posted(schedule_id=[first, second], base_digest="a" * 64)
    assert _chosen(chosen) == [first, second]
    too_many = [str(uuid4()) for _ in range(SCHEDULE_LIMIT + 1)]
    for refused, message in (
        (posted(schedule_id=[first, first]), "chosen more than once"),
        (posted(base_digest="a" * 64), "Choose at least one"),
        (posted(schedule_id=too_many), "at most 100"),
    ):
        with pytest.raises(UserFacingError) as error:
            _chosen(refused)
        assert message in str(error.value.refusal.message)
    with pytest.raises(ValueError):
        _chosen(posted(schedule_id=first, other="x"))
    with pytest.raises(ValueError):
        _chosen(posted(schedule_id="not-a-uuid"))


def render_actions(source, **context):
    """Render ``source`` with the stewardship tags loaded."""
    return Template("{% load stewardship %}" + source).render(Context(context))


def test_table_actions_draw_named_icon_buttons_only_for_given_actions():
    """Edit is a link, Delete a dialog button; each named for its row."""
    html = render_actions(
        '{% table_actions name="Reminder 2" edit="/edit/" delete="abc" '
        'confirm="schedule-delete" field="schedule_id" %}'
    )
    assert 'href="/edit/"' in html and 'aria-label="Edit Reminder 2"' in html
    assert 'title="Delete Reminder 2"' in html
    assert 'type="button"' in html and 'name="schedule_id" value="abc"' in html
    assert 'data-confirm-open="schedule-delete"' in html
    assert "<svg" in html and 'aria-hidden="true"' in html
    empty = render_actions('{% table_actions name="Initial invitation" %}')
    assert "<a" not in empty and "<button" not in empty
    assert '<td class="table-actions"></td>' in empty
    with pytest.raises(template.TemplateSyntaxError):
        render_actions('{% table_actions name="x" delete="abc" %}')


def test_table_actions_revoke_has_its_own_words_and_icon():
    """verb="revoke" names the action Revoke and draws a different icon (#879)."""
    revoke = render_actions(
        '{% table_actions name="nightly" delete="abc" confirm="session-revoke" '
        'field="session_id" verb="revoke" %}'
    )
    delete = render_actions(
        '{% table_actions name="nightly" delete="abc" confirm="session-revoke" '
        'field="session_id" %}'
    )
    assert 'aria-label="Revoke nightly"' in revoke
    assert 'title="Revoke nightly"' in revoke
    assert "Delete" not in revoke and 'aria-label="Delete nightly"' in delete
    assert "<circle" in revoke and "<circle" not in delete
    # The same square danger button either way.
    assert 'class="button-secondary icon-button icon-button-danger"' in revoke
    with pytest.raises(template.TemplateSyntaxError):
        render_actions(
            '{% table_actions name="x" delete="abc" confirm="d" verb="remove" %}'
        )
