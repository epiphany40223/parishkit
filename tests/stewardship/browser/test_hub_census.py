"""The Family hub's census tabs in the browser (#1678 slice 2).

The fixtures (``timeline_components``) serve the hub of a campaign with
census, from the census unit test's response: a Family tab and seven Member
tabs (a renamed Member with a Reviewer's value, a moved one, a deceased one,
one ParishSoft no longer lists, an unchanged one and two the Family added),
each also at its own ``?tab=`` address. These tests check that a census tab
loads its panel in place on its first choice, the stage chips and marks,
the Show unchanged disclosure (closed, moving nothing above it when opened),
a direct load of a Member tab, and that each panel is accessible and fits a
phone. The tab component's own behaviour is tested in test_tabs.py.
"""

import pytest

from .test_charts import assert_clean, watch
from .timeline_components import ADMIN, TAB
from .waits import focused, has_attribute, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "webkit"], indirect=True
)

AXE = """async () => (await axe.run(document, {
  runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa']}
})).violations.map(({id, impact, nodes}) => ({
  id, impact, html: nodes.map(node => node.html.slice(0, 300))
}))"""
# A mark outside every region, which only a full page load could lose.
MARK = "document.querySelector('h1').dataset.mark = 'kept'"
MARKED = "document.querySelector('h1').dataset.mark"


def tab(page, name):
    """The hub's tab named ``name``."""
    return page.get_by_role("tablist", name="Family information").get_by_role(
        "tab", name=name, exact=True
    )


def test_the_family_tab_loads_in_place_with_its_stage(page, component_origin):
    """First choice fetches the Family panel alone; no reload, no jump."""
    errors = watch(page)
    page.goto(component_origin + ADMIN)
    page.evaluate(MARK)
    family = tab(page, "Family")
    has_attribute(family, "aria-selected", "false")
    family.click()
    has_attribute(family, "aria-selected", "true")
    panel = page.get_by_role("tabpanel", name="Family")
    table = panel.get_by_role("table", name="Changed fields", exact=True)
    visible(table)
    row = table.get_by_role("row", name="Home address")
    assert "1 Old Road" in row.inner_text() and "9 Census Way" in row.inner_text()
    visible(row.locator(".stage-chip", has_text="To review"))
    # Unchanged fields wait, closed, behind Show unchanged.
    summary = panel.get_by_text("Show unchanged (2 fields)")
    visible(summary)
    assert panel.locator("details.hub-unchanged").evaluate("node => !node.open")
    assert page.evaluate(MARKED) == "kept"
    assert page.url == component_origin + TAB["family"]
    focused(family)
    assert_clean(page, errors)


def test_show_unchanged_opens_below_without_moving_its_toggle(page, component_origin):
    """Opening the disclosure moves nothing above or at its toggle."""
    page.goto(component_origin + TAB["family"])
    panel = page.get_by_role("tabpanel", name="Family")
    summary = panel.locator("details.hub-unchanged > summary")
    before = summary.bounding_box()
    changed = panel.get_by_role(
        "table", name="Changed fields", exact=True
    ).bounding_box()
    summary.click()
    unchanged = panel.get_by_role("table", name="Unchanged fields")
    visible(unchanged)
    assert unchanged.get_by_role("row", name="Opt out of all parish emails").count()
    assert summary.bounding_box()["y"] == pytest.approx(before["y"], abs=0.5)
    assert panel.get_by_role("table", name="Changed fields", exact=True).bounding_box()[
        "y"
    ] == pytest.approx(changed["y"], abs=0.5)


def test_member_tabs_are_named_and_show_marks_and_values(page, component_origin):
    """Member tabs carry names; marks, Reviewer's value and added Members."""
    errors = watch(page)
    page.goto(component_origin + ADMIN)
    for name in ("Adams, Requested", "Adams, Dee", "Adams, Gus", "Adams, Zed"):
        visible(tab(page, name))
    tab(page, "Adams, Requested").click()
    panel = page.get_by_role("tabpanel", name="Adams, Requested")
    table = panel.get_by_role("table", name="Changed fields", exact=True)
    visible(table.get_by_role("columnheader", name="Reviewer's value"))
    row = table.get_by_role("row", name="First name")
    assert "Anne" in row.inner_text() and "Ready to update" in row.inner_text()
    tab(page, "Adams, Dee").click()
    moved = page.get_by_role("tabpanel", name="Adams, Dee")
    visible(moved.locator(".status-badge", has_text="No longer in this household"))
    entered = moved.get_by_role("row", name="No longer in this household")
    visible(entered.locator(".stage-chip", has_text="Entered by hand"))
    assert moved.get_by_text("Show unchanged").count() == 0
    tab(page, "Adams, Zed").click()
    added = page.get_by_role("tabpanel", name="Adams, Zed")
    visible(added.locator(".status-badge", has_text="New Member"))
    visible(added.get_by_text("Adding this Member:"))
    visible(added.get_by_role("table", name="The Family's answers for this Member"))
    # The panels fetched so far stay; each id is its tab's own.
    assert page.locator("[role=tabpanel][data-tab-loaded]").count() == 4
    assert page.evaluate(
        "[...document.querySelectorAll('[id]')].map(n => n.id)"
        ".every((id, i, all) => all.indexOf(id) === i)"
    )
    assert_clean(page, errors)


@pytest.mark.parametrize("key", ["family", "member-1", "member-2", "member-7"])
def test_census_panels_are_accessible_and_fit_a_phone(
    page, component_origin, axe_source, key
):
    """A direct load opens the tab; axe finds nothing; no sideways page scroll."""
    errors = watch(page)
    page.goto(component_origin + TAB[key])
    has_attribute(page.locator(f"#family-tabs-tab-{key}"), "aria-selected", "true")
    panel = page.locator(f"#family-tabs-panel-{key}")
    visible(panel.get_by_role("link", name="Open Census changes"))
    details = panel.locator("details.hub-unchanged")
    if details.count():
        details.locator("summary").click()
    page.evaluate(axe_source)
    assert page.evaluate(AXE) == []
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert_clean(page, errors)
