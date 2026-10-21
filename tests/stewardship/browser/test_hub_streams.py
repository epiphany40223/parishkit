"""The Family hub's Ministry, Financial and last tabs in the browser (#1678).

Slices 3 and 4. The fixtures (``timeline_components``) serve the hub of a
campaign with every module: Member 1's tab lists its Ministry joins and
leaves, the Financial tab the Family's pledge and the last tab its
cannot-attend, cannot-participate and Additional information answers, each
also at its own ``?tab=`` address. These tests check that each loads in
place on its first choice with its stage chips, that the last tab's Show
unchanged disclosure moves nothing when opened, and that each panel is
accessible and fits a phone. The census parts are tested in
test_hub_census.py and the tab component in test_tabs.py.
"""

import pytest

from .test_charts import assert_clean, watch
from .test_hub_census import AXE, MARK, MARKED, tab
from .timeline_components import ADMIN, TAB
from .waits import focused, has_attribute, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "webkit"], indirect=True
)


def test_the_financial_tab_loads_the_pledge_in_place(page, component_origin):
    """First choice fetches the Financial panel alone; no reload, no jump."""
    errors = watch(page)
    page.goto(component_origin + ADMIN)
    page.evaluate(MARK)
    financial = tab(page, "Financial")
    financial.click()
    has_attribute(financial, "aria-selected", "true")
    panel = page.get_by_role("tabpanel", name="Financial")
    visible(panel.locator("p", has_text="This pledge:").locator(".stage-chip"))
    table = panel.get_by_role("table", name="The Family's pledge")
    row = table.get_by_role("row", name="Annual pledge (ParishSoft 2026)")
    assert "$1,000.00" in row.inner_text() and "$1,200.00" in row.inner_text()
    visible(table.get_by_role("row", name="Share methods").get_by_text("(not read)"))
    visible(panel.get_by_text("ParishSoft contributions for 2026: $900.00."))
    assert panel.get_by_text("Show unchanged").count() == 0
    assert page.evaluate(MARKED) == "kept"
    assert page.url == component_origin + TAB["financial"]
    focused(financial)
    assert_clean(page, errors)


def test_the_last_tab_lists_items_and_hides_the_rest(page, component_origin):
    """Each Yes and the item with a chip; Show unchanged opens in place."""
    errors = watch(page)
    page.goto(component_origin + ADMIN)
    page.evaluate(MARK)
    tab(page, "Additional").click()
    panel = page.get_by_role("tabpanel", name="Additional", exact=True)
    table = panel.get_by_role("table", name="Answers to handle")
    attend = table.get_by_role("row", name="Cannot attend Mass or prayer services")
    visible(attend.locator(".stage-chip", has_text="To review"))
    information = table.get_by_role("row", name="Additional information")
    assert "Please call about the choir" in information.inner_text()
    visible(information.locator(".stage-chip", has_text="To handle"))
    visible(
        table.get_by_role("row", name="Cannot participate in ministries: Adams, Zed")
    )
    summary = panel.locator("details.hub-unchanged > summary")
    # In view first, so the click scrolls nothing and only the disclosure
    # could move the toggle.
    summary.scroll_into_view_if_needed()
    before = summary.bounding_box()
    summary.click()
    unchanged = panel.get_by_role("table", name="Unchanged fields: nothing to handle")
    visible(
        unchanged.get_by_role("row", name="Cannot participate in ministries: Adams, Bo")
    )
    assert summary.bounding_box()["y"] == pytest.approx(before["y"], abs=0.5)
    visible(panel.get_by_role("link", name="Open this Additional information request"))
    assert page.evaluate(MARKED) == "kept"
    assert_clean(page, errors)


def test_a_member_tab_lists_its_ministry_joins_and_leaves(page, component_origin):
    """Under the census part: each request's baseline, answer and stage."""
    errors = watch(page)
    page.goto(component_origin + ADMIN)
    tab(page, "Adams, Requested").click()
    panel = page.get_by_role("tabpanel", name="Adams, Requested")
    table = panel.get_by_role("table", name="Ministry joins and leaves")
    choir = table.get_by_role("row", name="Adult Choir")
    assert "Not in this Ministry" in choir.inner_text() and "Join" in choir.inner_text()
    visible(choir.locator(".stage-chip", has_text="To review"))
    ushers = table.get_by_role("row", name="Ushers")
    visible(ushers.locator(".stage-chip", has_text="Ignored"))
    visible(ushers.get_by_text("Stayed in the Ministry"))
    visible(panel.get_by_role("link", name="Open Ministry requests"))
    # A Member with no request says so.
    tab(page, "Adams, Dee").click()
    moved = page.get_by_role("tabpanel", name="Adams, Dee")
    visible(moved.get_by_text("No Ministry joins or leaves requested."))
    assert_clean(page, errors)


@pytest.mark.parametrize("key", ["member-1", "financial", "other"])
def test_stream_panels_are_accessible_and_fit_a_phone(
    page, component_origin, axe_source, key
):
    """A direct load opens the tab; axe finds nothing; no sideways page scroll."""
    errors = watch(page)
    page.goto(component_origin + TAB[key])
    has_attribute(page.locator(f"#family-tabs-tab-{key}"), "aria-selected", "true")
    details = page.locator(f"#family-tabs-panel-{key} details.hub-unchanged")
    if details.count():
        details.locator("summary").click()
    page.evaluate(axe_source)
    assert page.evaluate(AXE) == []
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert_clean(page, errors)
