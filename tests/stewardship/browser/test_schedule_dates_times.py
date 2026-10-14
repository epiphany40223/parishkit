"""The date-change review's send times are the browser's (#558).

Dates and mail schedules' date-change review shows each saved schedule's
send date and time in the browser's time zone and posts what is typed with
that zone, for the server to convert to the campaign-local values a schedule
keeps. The fixture campaign is in New York; the browser here is in London,
which leaves summer time on October 25, 2054, a week before New York, so the
same 9:00 AM New York send reads 14:00 before that date and 13:00 after it.
"""

import json

import pytest
from django.http import QueryDict

from parishkit.stewardship.accounts.schedule_forms import Schedules

from .waits import visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "webkit"], indirect=True
)

PAGE = "/schedule-dates-dst"
REVIEW = "Review changes"


def watch(page):
    """Collect uncaught page errors, so a test can assert there were none."""
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    return failures


def form_data(page):
    """What Review changes would post, as the server's QueryDict."""
    pairs = page.evaluate(
        """() => [...new FormData(document.getElementById("schedule-dates-form"))]
            .map(([name, value]) => [name, String(value)])"""
    )
    data = QueryDict(mutable=True)
    for name, value in pairs:
        data.appendlist(name, value)
    return data


def shown(page, count=3):
    """Each saved row's date and time as the page shows them."""
    return [
        (
            page.locator(f'[name="schedules-{index}-date"]').input_value(),
            page.locator(f'[name="schedules-{index}-time"]').input_value(),
        )
        for index in range(count)
    ]


def test_saved_times_show_and_a_changed_time_posts_in_the_browser_zone(
    browser_engine, component_origin
):
    """Saved sends are shown in London time; a changed one round-trips.

    The fields are filled from each row's moment and made the fields'
    defaults, so the change gate holds Review changes until a time really
    changes. The page's own posted fields, run through the review's formset,
    keep the unchanged schedules as saved and move the second reminder
    (shown as 13:00 on October 26) to 15:00 London, 11:00 AM in New York.
    """
    from .conftest import SETUP_BROWSER_TIMES, SETUP_DST

    context = browser_engine.new_context(timezone_id="Europe/London")
    try:
        page = context.new_page()
        page.set_viewport_size({"width": 1280, "height": 900})
        failures = watch(page)
        page.goto(component_origin + PAGE)
        visible(page.get_by_text("time zone: Europe/London."))
        assert shown(page) == [
            ("2054-10-01", "14:00"),
            ("2054-10-24", "14:00"),
            ("2054-10-26", "13:00"),
        ]
        review = page.get_by_role("button", name=REVIEW)
        assert review.is_disabled()
        # Open the second reminder's editor from its row and change its time.
        page.locator("[data-schedule-edit]").nth(2).click()
        time = page.locator('[name="schedules-2-time"]')
        visible(time)
        time.fill("3pm")
        time.blur()
        assert time.input_value() == "15:00"
        assert review.is_enabled()
        data = form_data(page)
        assert data["zone"] == "Europe/London"
        assert not failures
    finally:
        context.close()
    owner = SETUP_DST["owner"]
    formset = Schedules(
        data,
        prefix="schedules",
        previous=SETUP_DST["previous"],
        templates=SETUP_DST["templates"],
        campaign_id=owner["id"],
        campaign=owner["values"],
        zone=data["zone"],
        **SETUP_BROWSER_TIMES,
    )
    assert formset.is_valid(), (formset.errors, formset.non_form_errors())
    [changed] = formset.patch()
    assert changed["id"] == SETUP_DST["previous"][2]["id"]
    assert (changed["values"]["date"], changed["values"]["time"]) == (
        "2054-10-26",
        "11:00:00",
    )


@pytest.mark.parametrize("reported", ["", "Etc/Unknown"])
def test_the_review_waits_for_a_known_browser_zone(
    browser_engine, component_origin, reported
):
    """Without a browser zone the send times can't be read: Review changes
    stays unavailable, even after a change, and the page says why."""
    context = browser_engine.new_context(timezone_id="Europe/London")
    try:
        page = context.new_page()
        # Init scripts run before the page's own and are not subject to its CSP.
        page.add_init_script(
            """{
            const original = Intl.DateTimeFormat.prototype.resolvedOptions;
            Intl.DateTimeFormat.prototype.resolvedOptions = function () {
                return {...original.call(this), timeZone: ZONE};
            };
        }""".replace("ZONE", json.dumps(reported))
        )
        failures = watch(page)
        page.goto(component_origin + PAGE)
        visible(page.locator("[data-zone-missing]"))
        assert not page.locator("[data-browser-zone-note]").is_visible()
        review = page.get_by_role("button", name=REVIEW)
        assert review.is_disabled()
        assert "schedule-dates-zone-missing" in review.get_attribute("aria-describedby")
        page.locator("[data-schedule-edit]").nth(1).click()
        time = page.locator('[name="schedules-1-time"]')
        visible(time)
        time.fill("4pm")
        time.blur()
        assert review.is_disabled()
        assert not failures
    finally:
        context.close()
