"""Both mail repeat panels draw the one shared repeat rule (#1129).

New scheduled email's Repeat (``schedule-repeat-entry.html``) and the
date-change review's Repeat a reminder (``schedule-repeat.html``) include
``components/repeat-rule.html``, so they offer the same choices in the same
order. The review's controls have no names, so nothing of its rule is posted.
"""

import re

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
