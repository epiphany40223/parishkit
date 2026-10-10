"""The ParishSoft refresh schedule editor's form, checks and words (#632).

The editor is the first writer of ``refresh_rules``. These tests pin the
rules that keep Production's refresh times unchanged until an Administrator
changes the schedule itself: a stored schedule shown as rules and posted
back (in any row order) is no change and keeps the stored keys exactly;
its problems are shown but never block other settings; a changed schedule
is derived from its rules and blocked by its problems.
"""

from datetime import UTC, datetime, timedelta

import pytest
from django.template.loader import render_to_string

from parishkit.stewardship.accounts.integration_forms import IntegrationForm
from parishkit.stewardship.accounts.refresh_schedule_forms import (
    PRESETS,
    RULE_ROWS,
    SKIP_ROWS,
    ScheduleEditor,
    _times_words,
    differences,
    editor_view,
    posted_names,
    presets_payload,
)
from parishkit.stewardship.accounts.source_cadence_schema import _validate_cadence
from parishkit.stewardship.source.refresh_rules import (
    check_schedule,
    converted_rules,
    daily_times,
    stored_settings,
)
from parishkit.stewardship.source.schedule_preview import durations, summarize

from .schedule_rows import rows, shown

ZONE = "America/New_York"
NOW = datetime(2026, 10, 9, 16, tzinfo=UTC)
# Production's schedule on 2026-10-09: listed full times and quarter-hour
# quick updates, stored before the editor existed.
PRODUCTION = {
    "organization_id": "1",
    "full_refresh": "daily",
    "nightly_time": "00:00",
    "full_refresh_times": ["00:00", "08:00", "10:00", "12:00", "14:00", "16:00"]
    + ["18:00", "20:00"],
    "delta_refresh": "quarter_hour",
}
# Existing schedules of every stored shape, including the oldest documents.
EXISTING = [
    PRODUCTION,
    {"organization_id": "1"},
    {"organization_id": "1", "nightly_time": "03:00"},
    {"organization_id": "1", "full_refresh": "hourly", "delta_refresh": "hourly"},
    {"organization_id": "1", "full_refresh": "quarter_hour", "delta_refresh": "off"},
    {
        "organization_id": "1",
        "nightly_time": "02:00",
        "full_refresh_times": ["02:00", "08:30"],
        "delta_refresh": "hourly",
    },
    # A kept off-quarter-hour time too close to its neighbours: today it
    # runs, and the page shows its problems without blocking other settings.
    {
        "organization_id": "1",
        "nightly_time": "00:00",
        "full_refresh_times": ["00:00", "23:50"],
        "delta_refresh": "quarter_hour",
    },
]
SCHEDULE_KEYS = (
    "full_refresh",
    "nightly_time",
    "full_refresh_times",
    "delta_refresh",
    "quick_refresh_times",
    "refresh_rules",
)


def reader(document, *, now, timezone):
    """The preview without a database: typical durations, no email windows."""
    return summarize(
        document,
        timezone=timezone,
        today=now.astimezone(UTC).date(),
        windows=(),
        measured=durations({}),
        margin=timedelta(minutes=30),
    )


def view(editor):
    """The editor's preview, summary and line beside Save."""
    return editor_view(editor, timezone=ZONE, now=NOW, preview_reader=reader)


def document(*rules, skips=(), switch=False):
    """A ``refresh_rules`` document."""
    return {
        "rules": list(rules),
        "skips": list(skips),
        "skip_around_family_emails": switch,
    }


def full_at(value):
    return {"kind": "full", "at": value}


def quick_at(value):
    return {"kind": "quick", "at": value}


def every(kind, step, start, end):
    return {"kind": kind, "every": step, "from": start, "to": end}


def page(stored):
    """The settings page's editor, rendered as the page draws it."""
    editor = ScheduleEditor(stored=stored)
    return render_to_string(
        "stewardship/refresh-schedule-editor.html",
        {
            "schedule": view(editor),
            "presets": PRESETS,
            "presets_payload": presets_payload(),
        },
    )


@pytest.mark.parametrize("stored", EXISTING)
def test_an_existing_schedule_posted_back_unchanged_keeps_its_keys_exactly(stored):
    """Shown as rules and saved as shown, the stored schedule is untouched.

    No new key is written: the scheduler keeps running the existing
    schedule exactly as before, whatever the page shows.
    """
    posted = shown(page(stored))
    editor = ScheduleEditor(posted, stored=stored)
    assert editor.posted and not editor.changed
    assert editor.is_valid()
    assert editor.settings() == {
        name: stored[name] for name in SCHEDULE_KEYS if name in stored
    }
    form = IntegrationForm(
        "parishsoft",
        {"base_digest": "a" * 64, "organization_id": "1"} | posted,
        stored=stored,
    )
    assert form.is_valid(), form.errors
    assert form.public_settings() == stored


@pytest.mark.parametrize("stored", EXISTING)
def test_the_shown_rules_run_todays_times(stored):
    """The rows the page shows give exactly the times the old scheduler runs."""
    from parishkit.stewardship.source.cadence import refresh_settings

    schedule = refresh_settings(stored)
    result = daily_times(converted_rules(stored))
    if schedule["frequency"] == "daily":
        assert result.full == schedule["full_refresh_times"]


def test_rows_in_another_order_are_no_change():
    """Moving rows around changes nothing the schedule runs."""
    stored = stored_settings(
        document(full_at("02:00"), every("quick", 60, "00:00", "23:00"))
    )
    posted = rows([every("quick", 60, "00:00", "23:00"), full_at("02:00")])
    editor = ScheduleEditor(posted, stored=stored)
    assert not editor.changed
    assert editor.settings() == stored


def test_a_request_without_the_rows_never_changes_the_schedule():
    """A save that never drew the editor (a key paste) keeps the schedule."""
    form = IntegrationForm(
        "parishsoft",
        {"base_digest": "a" * 64, "organization_id": "123"},
        stored=PRODUCTION,
    )
    assert form.is_valid(), form.errors
    assert not form.schedule.posted and not form.schedule.changed
    assert form.public_settings() == PRODUCTION | {"organization_id": "123"}


def test_a_changed_schedule_is_derived_from_its_rules():
    """The stored lists come from the rules; nothing posted sets them."""
    new = document(
        full_at("02:00"),
        every("full", 120, "08:00", "18:00"),
        every("quick", 60, "07:00", "19:00"),
        skips=[{"at": "13:00"}],
        switch=True,
    )
    posted = rows(new["rules"], new["skips"], switch=True)
    # Stale old fields cannot be posted through the page's closed field
    # list; the editor reads only its own.
    assert not {"full_refresh_times", "quick_refresh_times"} & posted_names()
    editor = ScheduleEditor(posted, stored=PRODUCTION)
    assert editor.changed and editor.is_valid(), editor.problems()
    settings = editor.settings()
    assert settings == stored_settings(new)
    assert settings["quick_refresh_times"] == ["07:00", "09:00", "11:00", "15:00"] + [
        "17:00",
        "19:00",
    ]
    assert check_schedule(settings) == []
    # The configuration schema accepts what the editor writes.
    _validate_cadence(settings)


def test_problems_on_an_unchanged_schedule_never_block_other_settings():
    """A kept 23:50 too close to 00:00 is shown, but blocks only a change."""
    stored = EXISTING[-1]
    posted = shown(page(stored))
    editor = ScheduleEditor(posted, stored=stored)
    shorts = [problem.short for problem in editor.problems()]
    assert "00:00 is too close to 23:50" in shorts
    assert not editor.changed and not editor.blocking and editor.is_valid()
    line = view(editor)
    assert line.status.startswith("Your current schedule has")
    assert "saving other settings keeps it as it is" in line.status
    # Changing the schedule now needs the problems fixed first.
    changed = ScheduleEditor(posted | {"rules-0-at": "01:00"}, stored=stored)
    assert changed.changed and changed.blocking and not changed.is_valid()
    assert view(changed).status.startswith("Fix ")
    with pytest.raises(ValueError):
        changed.settings()
    form = IntegrationForm(
        "parishsoft",
        {"base_digest": "a" * 64, "organization_id": "1"}
        | posted
        | {"rules-0-at": "01:00"},
        stored=stored,
    )
    assert not form.is_valid()


def test_the_kept_time_is_labelled_and_allowed_only_as_a_full_time():
    """A kept off-quarter-hour time may stay a full time, never become new."""
    stored = EXISTING[-1]
    html = page(stored)
    assert "Kept from the earlier schedule (not on the quarter hour)." in html
    posted = shown(html)
    quick = posted | {"rules-1-kind": "quick"}
    errors = [p.text for p in ScheduleEditor(quick, stored=stored).problems()]
    assert "At: Use :00, :15, :30 or :45." in errors
    added = rows([full_at("02:00"), full_at("02:10")])
    errors = [p.text for p in ScheduleEditor(added, stored=stored).problems()]
    assert errors == ["At: Use :00, :15, :30 or :45."]


@pytest.mark.parametrize(
    ("posted", "expected"),
    [
        (
            {"rules-0-at": "25:00"},
            "At: “25:00” has no hour 25: hours run from 0 to 23.",
        ),
        ({"rules-0-at": ""}, "At: Enter a time."),
        ({"rules-0-at": "2:10"}, "At: Use :00, :15, :30 or :45."),
        (
            {
                "rules-0-shape": "every",
                "rules-0-every": "60",
                "rules-0-start": "22:00",
                "rules-0-last": "02:00",
            },
            "A rule can't cross midnight. Use two rules: one up to 23:45 and one "
            "from 00:00.",
        ),
        (
            {
                "rules-0-shape": "every",
                "rules-0-start": "02:00",
                "rules-0-last": "04:00",
            },
            "Interval: Choose how often.",
        ),
    ],
)
def test_a_row_that_cannot_be_read_is_reported_at_that_row(posted, expected):
    """Each row's own problem names its field; the schedule is blocked."""
    base = rows([full_at("02:00")])
    editor = ScheduleEditor(base | posted, stored=PRODUCTION)
    problems = editor.problems()
    assert [(p.scope, p.index, p.text) for p in problems] == [("rules", 0, expected)]
    assert problems[0].short == f"Rule 1: {expected}"
    assert editor.blocking and editor.document() is None


def test_fields_the_rows_shape_does_not_use_are_ignored():
    """A value left in a hidden field says nothing about the schedule."""
    posted = rows([full_at("02:00")]) | {"rules-0-start": "nonsense"}
    editor = ScheduleEditor(posted, stored=PRODUCTION)
    assert editor.document() == document(full_at("02:00"))


def test_a_blank_added_row_is_a_problem_not_dropped():
    """Every posted row is read: a blank one asks for its time."""
    posted = rows([full_at("02:00")]) | {
        "rules-TOTAL_FORMS": "2",
        "rules-1-kind": "quick",
        "rules-1-shape": "at",
    }
    editor = ScheduleEditor(posted, stored=PRODUCTION)
    assert [(p.index, p.text) for p in editor.problems()] == [(1, "At: Enter a time.")]


def test_skip_rows_read_single_times_and_ranges():
    """A range's end is not skipped and must be after its start."""
    posted = rows(
        [every("full", 60, "00:00", "23:00")], [{"from": "12:00", "to": "14:00"}]
    )
    editor = ScheduleEditor(posted, stored=PRODUCTION)
    assert "12:00" not in editor.daily().full and "14:00" in editor.daily().full
    crossing = posted | {"skips-0-start": "22:00", "skips-0-last": "01:00"}
    (problem,) = ScheduleEditor(crossing, stored=PRODUCTION).problems()
    assert (problem.scope, problem.index) == ("skips", 0)
    assert problem.text.startswith("A skip can't cross midnight")


def test_validator_problems_are_placed_at_the_row_that_causes_them():
    """Spacing at the later time's row; skips at their own row."""
    posted = rows(
        [full_at("02:00"), every("quick", 60, "00:00", "23:00")], [{"at": "12:30"}]
    )
    editor = ScheduleEditor(posted, stored={"organization_id": "1"})
    problems = {(p.scope, p.index): p for p in editor.problems()}
    skip = problems[("skips", 0)]
    assert skip.text == "This matches no refresh." and skip.anchor == "skips-0"
    assert skip.short == "the skip at 12:30 matches no refresh"
    only = rows([full_at("02:00")], [{"from": "01:00", "to": "03:00"}])
    (problem,) = ScheduleEditor(only, stored={}).problems()
    assert problem.text == (
        "This skips 02:00, the only full refresh; keep at least one full refresh a day."
    )
    (general,) = ScheduleEditor(rows([quick_at("02:00")]), stored={}).problems()
    assert general.scope is None and "keep at least one full refresh" in general.text


def test_too_close_names_both_times_at_the_later_row():
    """The admin-portal spec's example, at the row giving 00:00."""
    stored = EXISTING[-1]
    editor = ScheduleEditor(stored=stored)
    rows_shown = editor.shown["rules"]
    (problem,) = [p for p in editor.problems() if p.short.startswith("00:00")]
    assert rows_shown[problem.index] == full_at("00:00")
    assert problem.text == (
        "00:00 is only 10 minutes after the 23:50 full refresh; refreshes must be "
        "at least 15 minutes apart."
    )


def test_the_switch_starts_off_for_an_existing_schedule_and_is_a_change():
    """Decision 19: a converted schedule never skips today, so it starts off."""
    html = page(PRODUCTION)
    assert 'name="skip_around_family_emails"' in html
    assert 'name="skip_around_family_emails" id="id_skip_around_family_emails" ' in html
    assert "checked" not in html.split('name="skip_around_family_emails"')[1][:120]
    posted = shown(html) | {"skip_around_family_emails": "on"}
    editor = ScheduleEditor(posted, stored=PRODUCTION)
    assert (
        editor.changed
        and editor.settings()["refresh_rules"]["skip_around_family_emails"]
    )
    words = differences(PRODUCTION, editor.document(), timezone=ZONE)
    assert words == [
        "Turns on skipping refreshes around Family emails: refreshes other than "
        "the nightly one are skipped while a reminder is being prepared and while "
        "a Family email is being sent.",
        "Hourly and quarter-hour refreshes follow UTC today, so on the day clocks "
        "go back the repeated hour runs twice. As listed parish times they run "
        "once, at the earlier time.",
    ]
    # A saved rules schedule shows its own switch.
    saved = stored_settings(document(full_at("02:00"), switch=True))
    assert ScheduleEditor(stored=saved).switch is True


def test_presets_give_what_their_descriptions_say():
    """Each preset's daily times, as the admin-portal spec lists them."""
    found = {preset.key: daily_times(document(*preset.rules)) for preset in PRESETS}
    assert found["nightly"].full == ("02:00",) and not found["nightly"].quick
    assert len(found["nightly_hourly"].quick) == 23
    assert "02:00" not in found["nightly_hourly"].quick
    assert len(found["every_2_hours"].full) == 12
    assert found["every_4_hours"].full == ("00:00", "04:00", "08:00", "12:00") + (
        "16:00",
        "20:00",
    )
    business = found["business_hours"]
    assert business.full == ("02:00", "08:00", "10:00", "12:00", "14:00") + (
        "16:00",
        "18:00",
    )
    assert business.quick == ("07:00", "09:00", "11:00", "13:00", "15:00") + (
        "17:00",
        "19:00",
    )
    for preset in PRESETS:
        rules = document(*preset.rules)
        assert check_schedule(stored_settings(rules)) == []
    payload = presets_payload()
    assert [item["key"] for item in payload] == [p.key for p in PRESETS]
    assert payload[1]["rules"][1] == {
        "kind": "quick",
        "shape": "every",
        "every": 60,
        "start": "00:00",
        "last": "23:00",
    }


def test_the_review_lists_times_added_and_removed_in_words():
    """Decision 22: words, not the stored settings' diff."""
    new = document(
        every("full", 120, "00:00", "20:00"), every("quick", 15, "00:00", "23:45")
    )
    words = differences(PRODUCTION, new, timezone=ZONE)
    assert words[0] == "Full refreshes added: 02:00, 04:00 and 06:00."
    assert words[1] == "Quick updates removed: 02:00, 04:00 and 06:00."
    assert "Full refreshes removed" not in " ".join(words)
    hourly = {"organization_id": "1", "full_refresh": "hourly"}
    every_hour = document(
        every("full", 60, "00:00", "23:00"), every("quick", 15, "00:00", "23:45")
    )
    words = differences(
        hourly, every_hour | {"skips": [{"at": "03:00"}]}, timezone=ZONE
    )
    assert words[0] == "Full refreshes removed: 03:00."
    assert (
        "The nightly full refresh, the one that runs even while Family emails are "
        "being sent, moves from 02:00 to 00:00." in words
    )
    assert any(
        item.startswith("Full refreshes every hour become listed") for item in words
    )
    kolkata = differences(hourly, every_hour, timezone="Asia/Kolkata")
    assert any("not a whole number of hours from UTC" in item for item in kolkata)
    assert not any("clocks go back" in item for item in kolkata)
    # Rules that give the same times change nothing the scheduler runs.
    same = stored_settings(document(full_at("02:00"), full_at("08:00")))
    assert differences(
        same, document(every("full", 360, "02:00", "08:00")), timezone=ZONE
    ) == ["The refresh times stay the same; only the rules that describe them change."]


def test_long_runs_of_times_read_as_ranges():
    """A quarter-hour list stays short in the review."""
    quarter = [f"{h:02d}:{m:02d}" for h in range(0, 8) for m in (15, 30, 45)]
    assert _times_words(["01:00", "05:00"]) == "01:00 and 05:00"
    assert _times_words(["01:00", "02:00", "03:00", "04:00", "09:30"]) == (
        "every hour from 01:00 to 04:00 and 09:30"
    )
    assert _times_words(sorted(quarter))


def test_the_line_beside_save_is_the_cost_when_nothing_is_wrong():
    """No problems: the summary line, with the cost and freshness below."""
    editor = ScheduleEditor(rows([full_at("02:00")]), stored=PRODUCTION)
    shown_view = view(editor)
    assert shown_view.status == "About 7 min of ParishSoft time a day."
    assert shown_view.preview["summary"].startswith(
        "About 7 min of ParishSoft time a day (0.5% of the day): 1 full refresh of "
        "about 7.3 minutes (typical; not yet measured) and 0 quick updates"
    )
    assert shown_view.preview["freshness"] == (
        "New ParishSoft data arrives at least every 1 day (02:00 to 02:00). If a "
        "full refresh is more than 30 minutes late, Administrators are alerted."
    )
    assert shown_view.nightly == "02:00"
    assert len(shown_view.preview["days"]) == 7


def test_the_page_draws_rows_templates_and_the_switch():
    """Every row can be removed; new rows clone a ``__prefix__`` template."""
    html = page(PRODUCTION)
    assert html.count('data-schedule-row="rules"') == 10  # 8 full, 1 quick, template
    assert 'id="rules-__prefix__"' in html and 'name="rules-__prefix__-kind"' in html
    # Each rule row, and the two templates.
    assert html.count("data-row-remove") == 11
    assert "Your current schedule, shown as rules." in html
    assert 'name="rules-TOTAL_FORMS" value="9"' in html
    assert "schedule's time zone, America/New_York: the current campaign's" in html
    # The text lists are never posted.
    assert 'name="refresh-text' not in html and "data-schedule-text=" in html


def test_the_check_answer_draws_each_row_and_the_verdict():
    """The live check's answer carries every row's messages and data-blocking."""
    stored = EXISTING[-1]
    posted = shown(page(stored)) | {"rules-0-at": "01:00"}
    editor = ScheduleEditor(posted, stored=stored)
    html = render_to_string(
        "stewardship/refresh-schedule-check.html", {"schedule": view(editor)}
    )
    assert 'data-blocking="true"' in html
    for index in range(len(editor.rules.forms)):
        assert f'data-check-row="rules-{index}"' in html
    assert "Fix " in html and 'href="#rules-' in html


def test_an_unreadable_time_is_said_once_at_its_field():
    """The time entry already refuses "25:00" at the field; the row does not
    repeat it, but the line beside Save still names it."""
    posted = rows([full_at("02:00"), full_at("03:00")]) | {
        "rules-0-at": "25:00",
        "rules-1-at": "03:10",
    }
    editor = ScheduleEditor(posted, stored=PRODUCTION)
    assert editor.row_problems("rules") == {1: ["At: Use :00, :15, :30 or :45."]}
    links, text = view(editor).links, view(editor).status
    assert text == "Fix 2 problems before saving:"
    assert [anchor for anchor, _ in links] == ["rules-0", "rules-1"]
    assert links[0][1].startswith("Rule 1: At: “25:00” has no hour 25")


def test_other_rows_keep_their_problems_while_one_is_half_typed():
    """A blank new skip does not hide the spacing problem at rule 1."""
    stored = EXISTING[-1]
    posted = shown(page(stored))
    count = int(posted["skips-TOTAL_FORMS"])
    posted |= {"skips-TOTAL_FORMS": str(count + 1), f"skips-{count}-shape": "at"}
    editor = ScheduleEditor(posted, stored=stored)
    found = {(p.scope, p.index, p.code) for p in editor.problems()}
    assert ("rules", 0, "too_close") in found
    assert ("skips", count, "") in found
    # An unread rule might be the full one: "no full refresh" waits for it.
    half = rows([quick_at("03:00")]) | {
        "rules-TOTAL_FORMS": "2",
        "rules-1-kind": "full",
        "rules-1-shape": "at",
    }
    codes = [p.code for p in ScheduleEditor(half, stored={}).problems()]
    assert "no_full" not in codes and codes == [""]


def test_the_row_caps_keep_a_save_within_the_posted_field_limit():
    """Every name a full editor may post, with the page's other fields, the
    token and the action, fits DATA_UPLOAD_MAX_NUMBER_FIELDS; past it Django
    refuses the whole request before any form can say what is wrong."""
    from django.conf import settings

    from parishkit.stewardship.accounts.integration_forms import InlineCredentialForm

    page_fields = IntegrationForm("parishsoft", stored=PRODUCTION).fields
    key_fields = InlineCredentialForm("parishsoft").fields
    posted = len(posted_names()) + len(page_fields) + len(key_fields) + 2
    assert posted <= settings.DATA_UPLOAD_MAX_NUMBER_FIELDS
    editor = ScheduleEditor(stored=PRODUCTION)
    assert (editor.rules.max_num, editor.skips.max_num) == (RULE_ROWS, SKIP_ROWS)
    assert f"rules-{RULE_ROWS - 1}-every" in posted_names()
    assert f"rules-{RULE_ROWS}-kind" not in posted_names()
    assert f"skips-{SKIP_ROWS}-at" not in posted_names()


def test_more_rows_than_the_cap_are_refused_in_plain_words():
    """One rule or skip past the cap is a problem of the whole schedule,
    said in full beside Save; Save stays unavailable."""
    quarter = [f"{h:02d}:{m:02d}" for h in range(24) for m in (0, 15, 30, 45)]
    many = rows([full_at(value) for value in quarter[: RULE_ROWS + 1]])
    editor = ScheduleEditor(many, stored=PRODUCTION)
    shown_view = view(editor)
    assert shown_view.general == [f"A schedule has at most {RULE_ROWS} rules."]
    assert shown_view.blocking
    skips = rows(
        [every("full", 15, "00:00", "23:45")],
        [{"at": value} for value in quarter[1 : SKIP_ROWS + 2]],
    )
    assert view(ScheduleEditor(skips, stored=PRODUCTION)).general == [
        f"A schedule has at most {SKIP_ROWS} skips."
    ]
    # At the cap itself, nothing is said about the count.
    capped = rows([full_at(value) for value in quarter[:RULE_ROWS]])
    assert not any(
        "at most" in text for text in view(ScheduleEditor(capped, stored={})).general
    )


def test_the_line_beside_save_says_whole_schedule_problems_in_full():
    """A problem without a row is said in the line beside Save, after the
    links, never drawn above the rows (#736)."""
    editor = ScheduleEditor(rows([quick_at("02:00")]), stored=PRODUCTION)
    shown_view = view(editor)
    assert shown_view.links == []
    line = render_to_string(
        "stewardship/refresh-schedule-status.html", {"schedule": shown_view}
    ).strip()
    assert line == (
        "Fix 1 problem before saving: Add a Full rule: keep at least one full "
        "refresh a day."
    )
    both = rows([quick_at("02:00")], [{"at": "12:30"}])
    line = render_to_string(
        "stewardship/refresh-schedule-status.html",
        {"schedule": view(ScheduleEditor(both, stored=PRODUCTION))},
    ).strip()
    assert line.startswith('Fix 2 problems before saving: <a href="#skips-0">')
    assert line.endswith("</a>. Add a Full rule: keep at least one full refresh a day.")
    html = page(PRODUCTION)
    assert "data-schedule-part=" not in html.split('data-schedule-rows="rules"')[0]


def test_the_editor_has_no_live_region_and_gives_the_script_its_words():
    """The line beside Save is the only live region; the script's words,
    the caps and the schedule's zone come from the template."""
    html = page(PRODUCTION)
    assert 'role="status"' not in html and "aria-live" not in html
    assert 'data-schedule-zone="America/New_York"' in html
    assert "data-browser-zone-text=" in html and "data-browser-time-text=" in html
    assert f"have {{count}} times, but a schedule has at most {RULE_ROWS} rules" in html
    assert f"A schedule has at most {RULE_ROWS} rules." in html
    assert f"A schedule has at most {SKIP_ROWS} skips." in html
