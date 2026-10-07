"""Report date filters are days in the browser's time zone (#558).

The Additional information queue and one Ministry's requests take their
submitted-date filters as the viewer's local days: the page names the zone,
Apply sends it with the dates, and a browser that reports no zone cannot
apply a date (the server would refuse it rather than guess).
"""

import json
from urllib.parse import parse_qs

import pytest
from django.http import QueryDict

from parishkit.stewardship.reports.information import InformationQuery
from parishkit.stewardship.reports.ministries import MinistryQuery

from .information_components import DATED_ZONE
from .waits import eventually, has_text, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "webkit"], indirect=True
)

ZONE_HINT = (
    "Your browser didn't report a time zone, so these dates can't be used. "
    "Clear the dates, or check your computer's time zone setting."
)
# (component page, zone note id, Apply's hint id, how the server parses it)
PAGES = (
    ("/information", "information-zone-help", "information-filter-hint", False),
    ("/ministry-detail", "ministry-zone-help", "ministry-filter-hint", True),
)


def _posted(request):
    """A captured form POST, as the server's own grammar receives it."""
    body = QueryDict(request.post_data, mutable=True)
    body.pop("csrfmiddlewaretoken", None)
    return body


@pytest.mark.parametrize(("path", "note_id", "hint_id", "ministry"), PAGES)
def test_dates_are_days_in_the_browser_zone(
    browser_engine, component_origin, path, note_id, hint_id, ministry
):
    """The note names this computer's zone and Apply sends it with the days."""
    context = browser_engine.new_context(timezone_id="Asia/Kathmandu")
    try:
        page = context.new_page()
        page.goto(component_origin + path)
        # The zone as this engine names it (Chromium reports the catalog
        # alias "Asia/Katmandu", which the server accepts too).
        zone = page.evaluate("Intl.DateTimeFormat().resolvedOptions().timeZone")
        visible(page.locator(f"#{note_id}").get_by_text(zone, exact=False))
        assert page.locator("label", has_text="parish date").count() == 0
        assert page.get_by_text("campaign's time zone", exact=False).count() == 0
        form = page.locator("#table-filters")
        assert form.locator("[name=zone]").input_value() == zone
        start = page.get_by_label("Submitted on or after", exact=True)
        assert start.get_attribute("aria-describedby") == note_id
        start.fill("2026-11-01")
        page.get_by_label("Submitted on or before", exact=True).fill("2026-11-02")
        apply = page.get_by_role("button", name="Apply filters")
        assert apply.is_enabled()
        page.route(
            "**/*",
            lambda route: (
                route.fulfill(body="Filtered")
                if route.request.method == "POST"
                else route.continue_()
            ),
        )
        with page.expect_request(lambda request: request.method == "POST") as sent:
            apply.click()
        assert "?" not in sent.value.url
        assert parse_qs(sent.value.post_data)["zone"] == [zone]
        body = _posted(sent.value)
        if ministry:
            body.pop("ministry", None)
            query = MinistryQuery.parse(body, detail=True)
        else:
            # The queue's view takes the rows-per-page choice off first.
            body.pop("size", None)
            query = InformationQuery.parse(body)
        assert (query.start, query.end, query.zone) == (
            "2026-11-01",
            "2026-11-02",
            zone,
        )
    finally:
        context.close()


@pytest.mark.parametrize(("path", "note_id", "hint_id", "ministry"), PAGES)
@pytest.mark.parametrize("reported", ["", "Etc/Unknown"])
def test_dates_wait_for_a_known_browser_zone(
    page, component_origin, path, note_id, hint_id, ministry, reported
):
    """Without a zone Apply waits, with a plain hint, only while a date is set."""
    # Init scripts run before the page's own and are not subject to its CSP.
    page.add_init_script(
        """{
        const original = Intl.DateTimeFormat.prototype.resolvedOptions;
        Intl.DateTimeFormat.prototype.resolvedOptions = function () {
            return {...original.call(this), timeZone: ZONE};
        };
    }""".replace("ZONE", json.dumps(reported))
    )
    page.goto(component_origin + path)
    apply = page.get_by_role("button", name="Apply filters")
    hint = page.locator(f"#{hint_id}")
    assert apply.is_enabled() and not hint.is_visible()
    assert page.locator("#table-filters [name=zone]").input_value() == ""
    assert not page.locator(f"#{note_id}").is_visible()
    for label in ("Submitted on or after", "Submitted on or before"):
        field = page.get_by_label(label, exact=True)
        assert field.get_attribute("aria-describedby") == hint_id
        field.fill("2026-09-19")
        assert apply.is_disabled()
        has_text(hint, ZONE_HINT)
        field.fill("")
        assert apply.is_enabled() and not hint.is_visible()


@pytest.mark.parametrize(
    ("path", "pattern", "applied"),
    [
        ("/information", "**/information/", "/information-dated"),
        ("/ministry-detail", "**/join/", "/ministry-dated"),
    ],
)
def test_the_export_carries_the_applied_zone(
    browser_engine, component_origin, path, pattern, applied
):
    """After Apply, the export form sends the zone the dates were applied in.

    Its zone field is table state the in-place refresh copies from the
    server's answer, not a field the page script fills with this browser's
    zone, so a later export repeats exactly the filters the table shows.
    """
    context = browser_engine.new_context(timezone_id=DATED_ZONE)
    try:
        page = context.new_page()
        page.goto(component_origin + path)
        export = page.locator("#table-export [name=zone]")
        assert export.count() == 1 and export.input_value() == ""
        assert export.get_attribute("data-browser-zone") is None
        page.get_by_label("Submitted on or after", exact=True).fill("2026-11-01")
        page.get_by_label("Submitted on or before", exact=True).fill("2026-11-02")
        # The server's answer: the page rendered with the applied filters.
        page.route(
            pattern,
            lambda route: (
                route.fulfill(
                    response=route.fetch(url=component_origin + applied, method="GET")
                )
                if route.request.method == "POST"
                else route.continue_()
            ),
        )
        with page.expect_request(lambda request: request.method == "POST") as sent:
            page.get_by_role("button", name="Apply filters").click()
        posted = parse_qs(sent.value.post_data)["zone"]
        assert posted == [DATED_ZONE]
        eventually(
            page,
            "document.querySelector('#table-export [name=zone]').value",
            DATED_ZONE,
        )
        assert export.get_attribute("data-browser-zone") is None
        assert page.locator("#table-export [name=start]").input_value() == "2026-11-01"
    finally:
        context.close()
