"""A new time zone choice starts on the browser's zone (#1330).

Create the campaign marks its fresh time zone menu ``data-browser-default``;
ui-v1.js selects the zone the browser reports when the menu offers it.
Otherwise the parish zone stays selected. The server side is tested in
``database/test_campaign_create_postgresql.py``.
"""

import pytest

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

# A browser that reports no time zone.
NO_ZONE = (
    "const options = Intl.DateTimeFormat.prototype.resolvedOptions;"
    "Intl.DateTimeFormat.prototype.resolvedOptions = function () {"
    "  return {...options.call(this), timeZone: undefined};"
    "};"
)
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
