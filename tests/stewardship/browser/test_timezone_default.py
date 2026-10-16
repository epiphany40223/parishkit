"""A new time zone choice starts on the browser's zone (#1330).

Create the campaign and the setup Parish step mark their fresh time zone
menu ``data-browser-default``; ui-v1.js selects the zone the browser
reports when the menu offers it. Otherwise Create the campaign keeps the
parish zone and the setup Parish step a blank required choice, never the
list's first zone (Africa/Abidjan was stored that way on pk-local). The
server side is tested in ``database/test_campaign_create_postgresql.py``
and ``test_canonical_timezone.py``.
"""

import json

import pytest

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

# A browser that reports ZONE (a JavaScript literal) as its time zone.
REPORTS = (
    "const options = Intl.DateTimeFormat.prototype.resolvedOptions;"
    "Intl.DateTimeFormat.prototype.resolvedOptions = function () {"
    "  return {...options.call(this), timeZone: ZONE};"
    "};"
)
# A browser that reports no time zone.
NO_ZONE = REPORTS.replace("ZONE", "undefined")
MENU = "select[name=timezone]"


def zone_page(browser_engine, zone):
    """A page whose browser reports ``zone`` (the ``page`` fixture's is fixed)."""
    context = browser_engine.new_context(timezone_id=zone, reduced_motion="reduce")
    return context, context.new_page()


def test_create_starts_on_the_browser_zone(page, component_origin):
    """The ``page`` fixture's browser is in America/Los_Angeles, not the
    parish's America/New_York; that zone is selected, and any other zone
    chosen is the one posted."""
    page.goto(component_origin + "/campaign-create")
    menu = page.locator(MENU)
    assert menu.input_value() == "America/Los_Angeles"
    menu.select_option("America/Kentucky/Louisville")
    posted = page.locator("[data-campaign-form]").evaluate(
        "form => new FormData(form).get('timezone')"
    )
    assert posted == "America/Kentucky/Louisville"


def test_create_starts_on_louisville_in_louisville(browser_engine, component_origin):
    """The Administrator's own zone is selected for a browser there."""
    context, page = zone_page(browser_engine, "America/Kentucky/Louisville")
    try:
        page.goto(component_origin + "/campaign-create")
        assert page.locator(MENU).input_value() == "America/Kentucky/Louisville"
    finally:
        context.close()


def test_create_falls_back_to_the_parish_zone(page, component_origin):
    """With no zone from the browser the parish zone stays selected."""
    page.add_init_script(NO_ZONE)
    page.goto(component_origin + "/campaign-create")
    assert page.locator(MENU).input_value() == "America/New_York"


def test_a_redrawn_create_form_keeps_its_zone(page, component_origin):
    """A form drawn again after a refusal (its posted zone the parish's) is
    not reset to the browser's America/Los_Angeles."""
    page.goto(component_origin + "/campaign-create-refused")
    menu = page.locator(MENU)
    assert menu.get_attribute("data-browser-default") is None
    assert menu.input_value() == "America/New_York"


def test_setup_parish_starts_on_the_browser_zone(page, component_origin):
    """A first Parish step starts on the browser's zone, not Africa/Abidjan."""
    page.goto(component_origin + "/setup-parish")
    assert page.locator(MENU).input_value() == "America/Los_Angeles"


def test_setup_parish_without_a_browser_zone_asks_for_one(page, component_origin):
    """With no zone from the browser the menu starts blank and is required:
    the browser refuses to submit it until a zone is chosen."""
    page.add_init_script(NO_ZONE)
    page.goto(component_origin + "/setup-parish")
    menu = page.locator(MENU)
    assert menu.input_value() == ""
    assert menu.locator("option:checked").inner_text() == "Choose a time zone"
    assert menu.evaluate("select => select.required && !select.checkValidity()")
    menu.select_option("America/Chicago")
    assert menu.evaluate("select => select.checkValidity()")


@pytest.mark.parametrize("utc", ["UTC", "Etc/UTC"])
def test_a_utc_browser_counts_as_no_zone(page, component_origin, utc):
    """A browser reporting UTC (a server, VM or privacy setting, rarely the
    parish's clock) is treated like one reporting no zone: Create the
    campaign keeps the parish zone, and the setup Parish step a blank
    required choice.

    The report is replaced, as for NO_ZONE, rather than set as the
    context's zone: Firefox's Playwright refuses Etc/UTC as a zone ID
    ("Invalid timezone ID"), and this way every engine reports exactly
    the name under test.
    """
    page.add_init_script(REPORTS.replace("ZONE", json.dumps(utc)))
    page.goto(component_origin + "/campaign-create")
    assert page.locator(MENU).input_value() == "America/New_York"
    page.goto(component_origin + "/setup-parish")
    assert page.locator(MENU).input_value() == ""
