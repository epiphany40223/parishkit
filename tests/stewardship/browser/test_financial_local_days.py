"""Financial stewardship response dates are days in the browser's zone (#558).

The first and latest response date filters (From and Through) take the
viewer's local days since migration 0110: the page names the zone, Apply
sends it with the dates, a browser that reports no zone cannot apply a date
(the server would refuse it rather than guess), and the export repeats the
zone the dates were applied in. No page error is raised along the way.
"""

import json
from urllib.parse import parse_qs

import pytest
from django.http import QueryDict

from parishkit.stewardship.reports.financial import FinancialQuery

from .financial_components import DATED_ZONE
from .waits import eventually, has_text, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "webkit"], indirect=True
)

ZONE_HINT = (
    "Your browser didn't report a time zone, so these dates can't be used. "
    "Clear the dates, or check your computer's time zone setting."
)
DATES = ("first-start", "first-end", "latest-start", "latest-end")


def _watch(page):
    """Collect the page's uncaught script errors."""
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    return failures


def test_response_dates_are_days_in_the_browser_zone(browser_engine, component_origin):
    """The note names this computer's zone, no legend names the campaign's,
    and Apply sends the zone with the four days."""
    context = browser_engine.new_context(timezone_id="Asia/Kathmandu")
    try:
        page = context.new_page()
        failures = _watch(page)
        page.goto(component_origin + "/financial-report")
        # The zone as this engine names it (Chromium reports the catalog
        # alias "Asia/Katmandu", which the server accepts too).
        zone = page.evaluate("Intl.DateTimeFormat().resolvedOptions().timeZone")
        visible(page.locator("#financial-zone-help").get_by_text(zone, exact=False))
        assert page.get_by_text("campaign timezone", exact=False).count() == 0
        form = page.locator("#table-filters")
        # The export's zone menu offers UTC (the recorded exception); the
        # filters never name a fixed zone.
        assert form.get_by_text("UTC", exact=True).count() == 0
        for legend in ("First response date", "Submitted date"):
            assert form.locator("legend", has_text=legend).count() == 1
        assert form.locator("[name=zone]").input_value() == zone
        for field_id, day in zip(DATES, ("2026-11-01", "2026-11-02") * 2, strict=True):
            field = page.locator(f"#{field_id}")
            assert field.get_attribute("aria-describedby") == "financial-zone-help"
            field.fill(day)
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
        body = QueryDict(sent.value.post_data, mutable=True)
        body.pop("csrfmiddlewaretoken", None)
        # The view takes the rows-per-page choice off first.
        body.pop("size", None)
        query = FinancialQuery.parse(body)
        assert (
            query.first_start,
            query.first_end,
            query.latest_start,
            query.latest_end,
            query.zone,
        ) == ("2026-11-01", "2026-11-02", "2026-11-01", "2026-11-02", zone)
        visible(page.get_by_text("Filtered", exact=True))
        assert failures == []
    finally:
        context.close()


@pytest.mark.parametrize("reported", ["", "Etc/Unknown"])
def test_dates_wait_for_a_known_browser_zone(page, component_origin, reported):
    """Without a zone Apply waits, with a plain hint, only while a date is set.

    The hint's line is reserved (data-complete-hint="reserve", #563), so the
    hint appearing never moves what follows it (no layout shift).
    """
    failures = _watch(page)
    # Init scripts run before the page's own and are not subject to its CSP.
    page.add_init_script(
        """{
        const original = Intl.DateTimeFormat.prototype.resolvedOptions;
        Intl.DateTimeFormat.prototype.resolvedOptions = function () {
            return {...original.call(this), timeZone: ZONE};
        };
    }""".replace("ZONE", json.dumps(reported))
    )
    page.goto(component_origin + "/financial-report")
    apply = page.get_by_role("button", name="Apply filters")
    hint = page.locator("#financial-filter-hint")
    # The hint's place on the page (not in the viewport, which scrolls to
    # each field as it is filled).
    where = "e => e.getBoundingClientRect().top + window.scrollY"
    top = hint.evaluate(where)
    assert apply.is_enabled() and hint.inner_text() == ""
    assert page.locator("#table-filters [name=zone]").input_value() == ""
    assert not page.locator("#financial-zone-help").is_visible()
    for field_id in DATES:
        field = page.locator(f"#{field_id}")
        assert field.get_attribute("aria-describedby") == "financial-filter-hint"
        field.fill("2026-09-19")
        assert apply.is_disabled()
        has_text(hint, ZONE_HINT)
        assert hint.evaluate(where) == top
        field.fill("")
        assert apply.is_enabled() and hint.inner_text() == ""
    # Other filters still apply without a zone.
    page.get_by_label("At most").fill("5000.00")
    assert apply.is_enabled()
    assert failures == []


def test_a_through_date_before_its_from_date_still_waits(
    browser_engine, component_origin
):
    """With a known zone, a Through date before its From date keeps its own
    order hint (#563), which the zone wiring must not clear."""
    context = browser_engine.new_context(timezone_id="America/Chicago")
    try:
        page = context.new_page()
        failures = _watch(page)
        page.goto(component_origin + "/financial-report")
        apply = page.get_by_role("button", name="Apply filters")
        page.locator("#latest-start").fill("2026-11-02")
        page.locator("#latest-end").fill("2026-11-01")
        page.locator("#latest-end").blur()
        assert apply.is_disabled()
        has_text(
            page.locator("#financial-filter-hint"),
            "Enter a latest response Through date no earlier than its From date,"
            " or clear it.",
        )
        page.locator("#latest-end").fill("2026-11-03")
        assert apply.is_enabled()
        assert failures == []
    finally:
        context.close()


def test_the_export_carries_the_applied_zone(browser_engine, component_origin):
    """After Apply, the export form sends the zone the dates were applied in.

    Its zone field is table state the in-place refresh copies from the
    server's answer, not a field the page script fills with this browser's
    zone, so a later export repeats exactly the filters the table shows.
    """
    context = browser_engine.new_context(timezone_id=DATED_ZONE)
    try:
        page = context.new_page()
        failures = _watch(page)
        page.goto(component_origin + "/financial-report")
        export = page.locator("#table-export [name=zone]")
        assert export.count() == 1 and export.input_value() == ""
        assert export.get_attribute("data-browser-zone") is None
        page.locator("#first-start").fill("2026-11-01")
        page.locator("#latest-end").fill("2026-11-02")
        # The server's answer: the page rendered with the applied filters.
        page.route(
            "**/financial/",
            lambda route: (
                route.fulfill(
                    response=route.fetch(
                        url=component_origin + "/financial-dated", method="GET"
                    )
                )
                if route.request.method == "POST"
                else route.continue_()
            ),
        )
        with page.expect_request(lambda request: request.method == "POST") as sent:
            page.get_by_role("button", name="Apply filters").click()
        assert parse_qs(sent.value.post_data)["zone"] == [DATED_ZONE]
        eventually(
            page,
            "document.querySelector('#table-export [name=zone]').value",
            DATED_ZONE,
        )
        assert export.get_attribute("data-browser-zone") is None
        first = page.locator("#table-export [name=first_start]")
        assert first.input_value() == "2026-11-01"
        assert failures == []
    finally:
        context.close()


def test_the_zone_refusal_says_to_reload(page, component_origin):
    """Dates that arrive without a zone get their own plain line."""
    failures = _watch(page)
    page.goto(component_origin + "/financial-error-zone")
    has_text(
        page.get_by_role("alert"),
        "The dates came without your computer's time zone. Go back, reload the"
        " page and apply the filters again; if this repeats, check your"
        " computer's time zone setting.",
    )
    assert failures == []
