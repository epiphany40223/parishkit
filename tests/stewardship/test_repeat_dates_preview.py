"""The repeat rules' live date list, drawn by the shared schedule preview (#2092).

ui-conventions "Schedule previews" and admin-portal "New and Edit scheduled
email": the page script works out a rule's dates and why any is not added;
``repeat_dates_preview`` draws them through ``occurrence_preview.build`` and
``components/occurrences.html``, so the list has the shared words, days,
struck lines and daylight-saving note. These tests build and render it
without a database; the view's admission is the shared one.
"""

import html as markup
import re
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from django.http import QueryDict
from django.template.loader import render_to_string

from parishkit.stewardship.accounts import repeat_dates_preview as module

NY = "America/New_York"
LA = "America/Los_Angeles"
NOW = datetime(2026, 10, 1, 12, tzinfo=UTC)
SCRIPT = (
    Path(__file__).parents[2]
    / "src/parishkit/stewardship/accounts/static/stewardship/ui-v1.js"
)


def draw(dates, *, zone=NY, campaign_zone=NY, clock="09:00", skipped=()):
    """The list's fragment for ``dates`` ((date, reason code) pairs)."""
    preview = module.preview(
        dates, zone=zone, campaign_zone=campaign_zone, clock=clock, now=NOW
    )
    return render_to_string(
        "stewardship/repeat-dates-preview.html",
        {"preview": preview, "skipped": list(skipped)},
    )


def text(html):
    """The fragment's words, tags dropped."""
    return markup.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html)).strip())


def test_each_date_is_a_line_naming_its_day_and_browser_time():
    """Compact and grouped by the browser's days: the day, the send's instant
    for the browser to write as a time, then what sends."""
    html = draw([(date(2026, 10, 19), ""), (date(2026, 10, 26), "")])
    assert html.startswith('<ul class="occurrence-list">')
    assert html.count("<li ") == 2
    assert (
        'Monday, October 19, 2026, <time datetime="2026-10-19T13:00:00+00:00" '
        "data-local-instant data-time-only>" in html
    )
    assert "</time> Reminder</li>" in html
    assert "<s>" not in html


def test_a_date_not_added_is_struck_with_its_reason_in_words():
    """Each reason code the page posts has the server's words; the limits
    named are the server's own."""
    html = draw(
        [
            (date(2026, 10, 19), "passed"),
            (date(2026, 10, 20), "outside"),
            (date(2026, 10, 21), "initial"),
            (date(2026, 10, 22), "taken"),
            (date(2026, 10, 23), "batch"),
            (date(2026, 10, 24), "room"),
        ]
    )
    assert html.count("<s>") == 6
    words = text(html)
    for reason in (
        "Reminder not added: its time has passed",
        "Reminder not added: outside the campaign dates",
        "Reminder not added: not after the initial invitation",
        "Reminder not added: another email is already scheduled then",
        f"Reminder not added: over the limit of {module.REPEAT_LIMIT} reminders at "
        "a time",
        f"Reminder not added: over the limit of {module.SCHEDULE_LIMIT} schedules",
    ):
        assert reason in words


def test_without_a_send_time_the_lines_name_only_their_day():
    """Until the time reads there is no instant: each line is its day and
    what sends."""
    html = draw([(date(2026, 10, 19), "")], clock=None)
    assert "<time" not in html
    assert text(html) == "Monday, October 19, 2026, Reminder"


def test_a_clock_change_names_the_day_and_adds_the_shared_note():
    """A day whose clocks change says so beside its name; a send in the
    repeated hour says it sends once, and the general sentence follows."""
    html = draw([(date(2026, 11, 1), "")], clock="01:30")
    words = text(html)
    assert "Sunday, November 1, 2026 (clocks go back)," in words
    assert "Reminder: sends once, clocks go back" in words
    assert "a time that happens twice sends once" in words


def test_the_campaign_zone_judges_the_shift():
    """A Los Angeles browser's 10:30 PM is New York's 1:30 AM on the day its
    clocks go back: the line says the campaign's clocks moved it."""
    words = text(draw([(date(2026, 10, 31), "")], zone=LA, clock="22:30"))
    assert "sends once because the campaign's clocks go back" in words


def test_an_unusable_browser_zone_is_the_unavailable_state():
    """The preview is not drawn and says why."""
    html = draw([(date(2026, 10, 19), "")], zone="")
    assert html.startswith('<p class="help">Your browser didn')
    assert "<li" not in html


def test_a_skipped_month_is_a_line_after_the_list():
    """A month without the rule's day is no send, so it follows the lines."""
    html = draw(
        [(date(2026, 10, 31), "")],
        skipped=module.skipped_months(["2026-11"], 31),
    )
    assert html.endswith(
        '</ul>\n<p class="help">November 2026 is skipped: it has no day 31.</p>\n\n'
    )


def posted(**fields):
    """A POST of the page script's fields (lists for the repeated ones)."""
    data = QueryDict(mutable=True)
    for name, value in fields.items():
        data.setlist(name, value if isinstance(value, list) else [value])
    return data


def test_read_takes_the_page_scripts_fields():
    """The dates pair with their reasons; the campaign zone must be real."""
    read = module._read(
        posted(
            zone=NY,
            campaign_zone=NY,
            time="09:00",
            date=["2026-10-19", "2026-10-26"],
            reason=["", "passed"],
            skipped=["2026-11"],
            day="31",
        )
    )
    assert read["dates"] == [(date(2026, 10, 19), ""), (date(2026, 10, 26), "passed")]
    assert read["months"] == ["2026-11"]
    assert (read["day"], read["clock"], read["campaign_zone"]) == (31, "09:00", NY)


@pytest.mark.parametrize(
    "fields",
    [
        {"date": ["2026-10-19"], "reason": []},
        {"date": ["2026-10-19"], "reason": ["unknown"]},
        {"date": ["2026-10-19"] * 401, "reason": [""] * 401},
        {"skipped": ["2026-11"], "day": "32"},
        {"campaign_zone": "Nowhere/City"},
        {"zone": [NY, LA]},
    ],
)
def test_read_refuses_what_the_page_never_posts(fields):
    """Unpaired, unknown, too many, a bad day or zone, a repeated field."""
    base = {"zone": NY, "campaign_zone": NY, "time": "09:00"}
    with pytest.raises(ValueError):
        module._read(posted(**(base | fields)))


def test_read_refuses_an_unknown_field():
    """Only the page script's own fields (the shared 400 refusal)."""
    with pytest.raises(ValueError, match="did not expect"):
        module._read(posted(zone=NY, campaign_zone=NY, other="x"))


def test_the_page_script_has_no_words_of_its_own_for_the_list():
    """The reasons' words are the server's (REASONS); the script posts codes."""
    script = SCRIPT.read_text()
    for reason in module.REASONS.values():
        assert str(reason).removeprefix("not added: ").split(" %")[0] not in script
