"""Both mail repeat panels draw the one shared repeat rule (#1129).

New scheduled email's Repeat (``schedule-repeat-entry.html``) and the
date-change review's Repeat a reminder (``schedule-repeat.html``) include
``components/repeat-rule.html``, so they offer the same choices in the same
order. The review's controls have no names, so nothing of its rule is posted.
"""

import re

import pytest
from django.http import QueryDict
from django.template.loader import render_to_string

from parishkit.stewardship.accounts.schedule_entry_views import RepeatRule
from parishkit.stewardship.accounts.schedule_forms import Schedules

from .campaign_factory import campaign

VALUES = {"start_date": "2054-10-01", "end_date": "2054-10-31"}


def review_panel():
    """The date-change review's Repeat a reminder, as schedule-fields.html draws it."""
    owner = campaign()
    schedules = Schedules(
        prefix="schedules",
        previous=[],
        templates=[],
        campaign_id=owner["id"],
        campaign=owner["values"],
    )
    return render_to_string(
        "stewardship/schedule-repeat.html", {"schedules": schedules}
    )


def entry_panel(repeat):
    """New scheduled email's Repeat, with the context its view passes."""
    return render_to_string(
        "stewardship/schedule-repeat-entry.html",
        {
            "repeat": repeat,
            "values": VALUES,
            "window_start": "2054-10-01T04:00:00+00:00",
            "window_end": "2054-11-01T04:00:00+00:00",
            "mailings_json": "[]",
            "room": 3,
            "limit": 30,
        },
    )


def rule_controls(html):
    """The rule's controls, from How often to Until, without names or values.

    What is left is what both panels must share: the ids, data-repeat hooks,
    labels, choices and their order.
    """
    start = html.index('<div class="field"><label for="repeat-frequency">')
    end = html.index("\n", html.index('<label for="repeat-until">'))
    controls = html[start:end]
    controls = re.sub(r' name="[^"]*"', "", controls)
    controls = re.sub(r" (selected|checked)", "", controls)
    return re.sub(
        r' (type="date" data-repeat="(?:from|until)") value="[^"]*"', r" \1", controls
    )


def test_both_panels_draw_the_same_repeat_rule():
    """The review's rule and New's rule differ only in names and drawn values."""
    review = review_panel()
    entry = entry_panel(RepeatRule(prefix="repeat"))
    assert rule_controls(review) == rule_controls(entry)
    # Every weekday is offered Monday first, in both the weekly days and the
    # monthly weekday (wall_clock.WEEKDAYS).
    days = re.findall(r'data-repeat="weekday" value="(\d)"', review)
    assert days == [str(day) for day in range(7)]
    assert ">Monday</option>" in review and "> Monday</label>" in review


def test_the_review_rule_posts_nothing():
    """The review panel only adds rows in the page: no rule control has a name.

    Its From and Until start empty; the page script fills them from the
    proposed campaign dates.
    """
    review = review_panel()
    # The whole panel: the shared rule, the send time and the email.
    assert re.findall(r"<(?:input|select)[^>]*\bname=", review) == []
    assert re.search(r'id="repeat-from" type="date" data-repeat="from">', review)
    assert re.search(r'id="repeat-until" type="date" data-repeat="until">', review)


def test_the_entry_rule_draws_what_was_posted():
    """New's refused page draws the posted rule again, by its prefixed names."""
    data = QueryDict(mutable=True)
    data.update(
        {
            "repeat-repeat": "on",
            "repeat-frequency": "monthly",
            "repeat-by": "weekday",
            "repeat-day": "5",
            "repeat-ordinal": "-1",
            "repeat-month_weekday": "3",
            "repeat-start": "2054-10-02",
            "repeat-until": "2054-10-20",
        }
    )
    data.setlist("repeat-weekdays", ["1", "4"])
    repeat = RepeatRule(data, prefix="repeat")
    assert repeat.is_valid(), repeat.errors
    html = entry_panel(repeat)
    assert '<option value="monthly" selected>' in html
    assert '<option value="weekday" selected>' in html
    assert '<option value="-1" selected>' in html
    assert 'name="repeat-day" type="number"' in html and 'value="5"' in html
    checked = re.findall(
        r'name="repeat-weekdays" data-repeat="weekday" value="(\d)" checked', html
    )
    assert checked == ["1", "4"]
    assert (
        'name="repeat-start" type="date" data-repeat="from" value="2054-10-02"' in html
    )
    assert (
        'name="repeat-until" type="date" data-repeat="until" value="2054-10-20"' in html
    )


def test_the_entry_rule_starts_on_the_campaign_dates():
    """A blank New starts From and Until on the campaign's own calendar dates."""
    html = entry_panel(RepeatRule(prefix="repeat"))
    assert 'data-repeat="from" value="2054-10-01"' in html
    assert 'data-repeat="until" value="2054-10-31"' in html


def bound(**values):
    """A bound New Repeat rule from ``values`` (field name -> posted value)."""
    data = QueryDict(mutable=True)
    for name, value in values.items():
        if isinstance(value, list):
            data.setlist(f"repeat-{name}", value)
        else:
            data[f"repeat-{name}"] = value
    return RepeatRule(data, prefix="repeat")


def marked(html, control_id):
    """The aria-describedby of ``control_id`` when it is drawn invalid, else None."""
    tag = re.search(rf'<(?:input|select)[^>]* id="{control_id}"[^>]*>', html)[0]
    if 'aria-invalid="true"' not in tag:
        return None
    return re.search(r'aria-describedby="([^"]*)"', tag)[1]


def test_an_unrefused_rule_reserves_its_error_lines_empty():
    """Every error line is drawn, hidden and empty, and nothing is marked
    (#1915); the lines the page's own controls can fill are reserved."""
    for html in (review_panel(), entry_panel(RepeatRule(prefix="repeat"))):
        assert 'aria-invalid="true"' not in html
        for line in ("from", "until", "weekdays", "day", "frequency", "month-weekday"):
            assert (
                f'<ul class="errorlist" id="repeat-{line}_error" hidden><li></li></ul>'
                in html
            )
        assert html.count('class="field-error-line"') == 4


def test_until_before_from_is_refused_at_both_and_said_once():
    """The server's refusal lands where the page marks it (#1915)."""
    repeat = bound(
        repeat="on", frequency="daily", start="2054-10-20", until="2054-10-10"
    )
    assert repeat.is_valid(), repeat.errors
    assert repeat.refuse_rule()
    assert [error.code for error in repeat.errors.as_data()["until"]] == [
        "repeat_order"
    ]
    html = entry_panel(repeat)
    assert marked(html, "repeat-from") == "repeat-until_error"
    assert marked(html, "repeat-until") == "repeat-until_error"
    assert html.count("The last date is before the first.") == 1
    assert re.search(
        r'<ul class="errorlist" id="repeat-until_error"><li>The last date is before '
        r"the first.</li></ul>",
        html,
    )
    assert '<ul class="errorlist" id="repeat-from_error" hidden>' in html


@pytest.mark.parametrize(
    ("values", "line", "controls"),
    [
        (
            {"frequency": "daily", "until": "2054-10-10"},
            "repeat-from_error",
            ["repeat-from"],
        ),
        ({"frequency": "daily"}, "repeat-until_error", ["repeat-from", "repeat-until"]),
        (
            {"frequency": "weekly", "start": "2054-10-01", "until": "2054-10-31"},
            "repeat-weekdays_error",
            [f"repeat-weekday-{day}" for day in range(7)],
        ),
        (
            {"frequency": "daily", "start": "2054-10-01", "until": "2055-10-31"},
            "repeat-until_error",
            ["repeat-from", "repeat-until"],
        ),
    ],
)
def test_each_rule_error_is_refused_at_its_fields(values, line, controls):
    """A missing date at the empty one(s), no weekday at the days, a span
    over a year at both dates (#1915; ui-v1.js repeatErrorPlace)."""
    repeat = bound(repeat="on", **values)
    assert repeat.is_valid(), repeat.errors
    assert repeat.refuse_rule()
    html = entry_panel(repeat)
    for control in controls:
        assert marked(html, control) == line
    others = {"repeat-from", "repeat-until", "repeat-day"} - set(controls)
    assert all(marked(html, control) is None for control in others)
    assert re.search(rf'<ul class="errorlist" id="{line}"><li>[^<]+</li></ul>', html)


def test_a_day_out_of_range_is_refused_at_the_day_in_the_page_words():
    """The day's own field check speaks the page script's words (#1915)."""
    repeat = bound(
        repeat="on",
        frequency="monthly",
        by="day",
        day="40",
        start="2054-10-01",
        until="2054-10-31",
    )
    assert not repeat.is_valid()
    html = entry_panel(repeat)
    assert marked(html, "repeat-day") == "repeat-day-help repeat-day_error"
    assert (
        '<ul class="errorlist" id="repeat-day_error"><li>Choose a day of the month '
        "from 1 to 31, or a weekday.</li></ul>"
    ) in html


def test_a_valid_rule_is_not_refused_at_its_fields():
    """A rule with dates is left to the Repeat checkbox's own refusal."""
    repeat = bound(
        repeat="on", frequency="daily", start="2054-10-01", until="2054-10-02"
    )
    assert repeat.is_valid(), repeat.errors
    assert not repeat.refuse_rule()
    assert repeat.marks == {}
