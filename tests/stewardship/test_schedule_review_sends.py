"""The mail schedule review's send times, drawn by the shared preview (#1129).

admin-portal "Dates and mail schedules" and ui-conventions "Schedule
previews" (ADM-04 slice 7c): each side of a schedule change lists its next
five sends after now in the compact layout, grouped by the browser's days;
a passed send is the empty state; paused
live delivery adds the notice; a review with no zone lists full
browser-local instants; the daylight-saving words name the campaign's wall
time only when the browser shares the campaign's zone.
"""

from datetime import UTC, datetime

from django.template.loader import render_to_string
from django.urls import reverse

from parishkit.stewardship.accounts.schedule_changes import describe

from .campaign_factory import campaign, schedule

NY = "America/New_York"
LA = "America/Los_Angeles"
# Before the factory campaign (October 2054), so every send is ahead.
BEFORE = datetime(2054, 9, 1, tzinfo=UTC)


def side(values, owner, **options):
    """One reviewed side and its rendered summary."""
    value = describe(values, owner["values"], **({"now": BEFORE} | options))
    html = render_to_string("stewardship/schedule-summary.html", {"value": value})
    return value, html


def lines(value):
    """The preview's lines, as ``(day, at, text, shift_text)``."""
    return [
        (day["date"], item["at"], item["text"], item["shift_text"])
        for day in value["occurrences"]["days"]
        for item in day["items"]
    ]


def test_a_dated_email_lists_its_one_send_on_its_browser_day():
    """One compact line, its day in words and a time-only browser instant;
    no ISO key, no more line and no daylight-saving sentence."""
    owner = campaign()
    value, html = side(schedule(owner["id"])["values"], owner, zone=LA)
    assert value["occurrences"]["state"] == "shown"
    assert value["occurrences"]["layout"] == "compact"
    assert value["resolved_slots"] == [
        {"key": "once", "due_at": "2054-10-01T13:00:00+00:00"}
    ]
    assert '<ul class="occurrence-list"><li class="schedule-time-initial">' in html
    assert (
        "Thursday, October 1, 2054, "
        '<time datetime="2054-10-01T13:00:00+00:00" data-local-instant '
        "data-time-only>2054-10-01T13:00:00+00:00</time> Initial invitation</li>"
    ) in html
    assert "When it sends (shown in your computer's time zone):" in html
    for gone in (
        "once —",
        "Only the",
        "When clocks change",
        "not a list of who will receive it",
    ):
        assert gone not in html


def test_a_passed_send_is_the_empty_state():
    """Nothing ahead: the empty line, no list and no "When it sends"."""
    owner = campaign()
    value, html = side(
        schedule(owner["id"])["values"],
        owner,
        zone=NY,
        now=datetime(2054, 10, 2, tzinfo=UTC),
    )
    assert value["resolved_slots"] == [] and value["more_slots"] is False
    assert value["occurrences"]["state"] == "empty"
    assert (
        '<p class="help">No more sends: this email&#x27;s time has passed.</p>' in html
    )
    assert "When it sends" not in html and "<ul" not in html


def test_paused_delivery_lists_the_sends_under_the_notice():
    """The sends stay listed, not struck, under the notice and its link."""
    owner = campaign()
    _value, html = side(schedule(owner["id"])["values"], owner, zone=NY, paused=True)
    assert (
        '<p class="notice">Live campaign email is paused, so these sends are '
        "held. Pause and resume mail shows what sends when mail resumes. "
        f'<a href="{reverse("admin:delivery_control")}">Pause and resume mail</a></p>'
    ) in html
    assert html.index("Live campaign email is paused") < html.index("<ul ")
    assert "<s>" not in html


def test_a_review_without_a_zone_lists_full_browser_instants():
    """The command line and Campaign settings' end-date review pass no zone:
    no day names, each line a full data-local-instant."""
    owner = campaign()
    value, html = side(schedule(owner["id"])["values"], owner)
    assert value["occurrences"]["grouped"] is False
    assert (
        '<li class="schedule-time-initial"><time '
        'datetime="2054-10-01T13:00:00+00:00" data-local-instant>'
        "2054-10-01T13:00:00+00:00</time> Initial invitation</li>"
    ) in html
    assert "data-time-only" not in html


def test_an_unusable_posted_zone_is_the_unavailable_state():
    """A posted zone the server cannot use draws no list and says why."""
    owner = campaign()
    value, html = side(schedule(owner["id"])["values"], owner, zone="")
    assert value["occurrences"]["state"] == "unavailable"
    assert "Your browser didn&#x27;t report a time zone" in html
    assert "<time" not in html


def dst_campaign():
    """A campaign whose clocks go back on Sunday, November 1, 2054 (New York)."""
    return campaign(start_date="2054-10-01", end_date="2054-11-30")


def test_daylight_saving_words_name_the_wall_time_only_in_the_campaigns_zone():
    """A send in the repeated hour: "sends once, clocks go back" when the
    browser shares the campaign's zone; otherwise only the campaign's
    clocks are named. The day note speaks of the shown zone, and the
    general sentence shows."""
    owner = dst_campaign()
    values = schedule(owner["id"], kind="reminder", date="2054-11-01", time="01:30:00")[
        "values"
    ]
    same, html = side(values, owner, zone=NY)
    assert lines(same)[0][2:] == ("Reminder", "sends once, clocks go back")
    assert "(clocks go back)" in html
    assert "When clocks change for daylight saving" in html
    other, html = side(values, owner, zone=LA)
    assert lines(other)[0][2:] == (
        "Reminder",
        "sends once because the campaign's clocks go back",
    )
    assert "1:30" not in html


def test_a_send_in_the_missing_hour_says_when_it_really_sends():
    """Clocks go forward: the same-zone line names the set time and the
    real one; another zone says only that it sends later than set."""
    owner = campaign(start_date="2055-03-01", end_date="2055-03-31", year_label="2056")
    values = schedule(owner["id"], kind="reminder", date="2055-03-14", time="02:30:00")[
        "values"
    ]
    same, _html = side(values, owner, zone=NY, now=datetime(2055, 2, 1, tzinfo=UTC))
    assert lines(same)[0][2:] == (
        "Reminder, set for 2:30 AM",
        "sends at 3:00 AM, clocks go forward",
    )
    other, _html = side(values, owner, zone=LA, now=datetime(2055, 2, 1, tzinfo=UTC))
    assert lines(other)[0][2:] == (
        "Reminder",
        "sends later than set because the campaign's clocks go forward",
    )
