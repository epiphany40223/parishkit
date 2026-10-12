"""The first-campaign step's send times are the browser's (#558).

Initial setup's Dates and mail schedules shows each saved schedule's send
date and time in the browser's time zone and posts what is typed with that
zone, for the server to convert to the campaign-local values a schedule
keeps. The fixture campaign is in New York; the browser here is in London,
which leaves summer time on October 25, 2054, a week before New York, so the
same 9:00 AM New York send reads 14:00 before that date and 13:00 after it.
"""

import json
from datetime import UTC, datetime

import pytest
from django.http import QueryDict

from parishkit.stewardship.accounts.schedule_forms import Schedules

from .waits import visible

pytestmark = pytest.mark.parametrize("browser_engine", ["chromium"], indirect=True)

PAGE = "/setup-schedules-dst"
SAVE = "Save and continue"


def watch(page):
    """Collect uncaught page errors, so a test can assert there were none."""
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    return failures


def form_data(page):
    """What Save and continue would post, as the server's QueryDict."""
    pairs = page.evaluate(
        """() => [...new FormData(document.querySelector("form[data-schedule-zoned]"))]
            .map(([name, value]) => [name, String(value)])"""
    )
    data = QueryDict(mutable=True)
    for name, value in pairs:
        data.appendlist(name, value)
    return data


def test_saved_times_show_and_typed_times_post_in_the_browser_zone(
    browser_engine, component_origin
):
    """Saved sends are shown in London time; a typed one round-trips.

    The page's own posted fields, run through the step's formset, keep every
    saved schedule unchanged and add the typed reminder (October 27 at 14:00
    in London, after London's change but before New York's) as 10:00 AM in
    New York.
    """
    from .conftest import SETUP_DST

    context = browser_engine.new_context(timezone_id="Europe/London")
    try:
        page = context.new_page()
        page.set_viewport_size({"width": 1280, "height": 900})
        failures = watch(page)
        page.goto(component_origin + PAGE)
        visible(page.get_by_text("time zone: Europe/London."))
        shown = [
            (
                page.locator(f'[name="schedules-{index}-date"]').input_value(),
                page.locator(f'[name="schedules-{index}-time"]').input_value(),
            )
            for index in range(3)
        ]
        assert shown == [
            ("2054-10-01", "14:00"),
            ("2054-10-24", "14:00"),
            ("2054-10-26", "13:00"),
        ]
        assert (
            "Reads as 13:00 (1:00 PM)"
            in page.locator("#id_schedules-2-time_reading").inner_text()
        )
        new = "schedules-3-"
        page.locator(f'[name="{new}kind"]').select_option("reminder")
        page.locator(f'[name="{new}date"]').fill("2054-10-27")
        page.locator(f'[name="{new}time"]').fill("2pm")
        page.locator(f'[name="{new}time"]').blur()
        reminder = SETUP_DST["templates"][1]["id"]
        page.locator(f'[name="{new}template_version"]').select_option(reminder)
        assert page.get_by_role("button", name=SAVE).is_enabled()
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
        browser_times=True,
        zone=data["zone"],
        now=datetime(2054, 9, 1, 12, tzinfo=UTC),
    )
    assert formset.is_valid(), (formset.errors, formset.non_form_errors())
    [added] = formset.patch()
    assert added["operation"] == "add"
    assert (added["values"]["date"], added["values"]["time"]) == (
        "2054-10-27",
        "10:00:00",
    )


@pytest.mark.parametrize("reported", ["", "Etc/Unknown"])
def test_the_step_waits_for_a_known_browser_zone(page, component_origin, reported):
    """Without a browser zone the send times can't be read: Save and continue
    stays unavailable and the page says why."""
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
    save = page.get_by_role("button", name=SAVE)
    assert save.is_disabled()
    assert "setup-schedules-zone-missing" in save.get_attribute("aria-describedby")
    # A readable time does not make it available.
    page.locator('[name="schedules-0-time"]').fill("9am")
    page.locator('[name="schedules-0-time"]').blur()
    assert save.is_disabled()
    assert not failures
