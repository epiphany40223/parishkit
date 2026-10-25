"""The Family hub's row actions in the browser (#2064).

The fixtures (``timeline_components``) serve the hub's Family tab with its
home address change decorated as Census changes decorates it (``HUB_ACTIONS``)
and the same tab once that change is accepted (``HUB_ACCEPTED``). These tests
check that a row's icon opens Census changes' own dialog with its words,
that Record posts the decision to Census changes, that the tab then redraws
itself in place from its own address (no reload, no jump, nothing above the
row moved) with focus back on the decision's icon, now selected, and that
the tab stays accessible and fits a phone. ``HUB_PLEDGE`` serves the
Financial tab with an edited pledge, whose Reviewer's value is a column of
the pledge's table, as an edited census field's is (R46 Part J).
"""

import pytest

from .test_charts import assert_clean, watch
from .test_table_sorting import MARK, MARKED
from .timeline_components import (
    HUB_ACCEPTED,
    HUB_ACTIONS,
    HUB_PLEDGE,
    HUB_PLEDGE_FRESH,
)
from .waits import focused, has_attribute, has_text, hidden, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "webkit"], indirect=True
)

AXE = """async () => (await axe.run(document, {
  runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa']}
})).violations.map(({id, impact, nodes}) => ({
  id, impact, html: nodes.map(node => node.html.slice(0, 300))
}))"""
CENSUS = "/admin/reports/census/"
PLEDGES = "/admin/reports/financial/pledges/"
NOTICE = "Home address for Family DUID 1234 accepted."


def home(page):
    """The Family tab's Home address row."""
    panel = page.locator("#family-tabs-panel-family")
    return panel.get_by_role("table", name="Changed fields", exact=True).get_by_role(
        "row", name="Home address"
    )


def answer_decisions(page, origin):
    """Answer a POST to Census changes as its page does (a notice), and the
    tab's own address, which the dialog redraws from, with the accepted
    tab. Returns the POST bodies sent."""
    sent = []

    def census(route):
        """Keep the decision's body; answer with a page carrying its notice."""
        if route.request.method != "POST":
            route.continue_()
            return
        sent.append(route.request.post_data)
        route.fulfill(
            content_type="text/html",
            body=f"<!doctype html><p data-confirm-announce>{NOTICE}</p>",
        )

    def tab(route):
        """The tab's address after the decision: the accepted tab."""
        route.fulfill(response=route.fetch(url=origin + HUB_ACCEPTED, method="GET"))

    page.route("**" + CENSUS, census)
    page.route(lambda url: url.endswith("?tab=family"), tab)
    return sent


def test_accept_from_the_family_tab_redraws_it_in_place(page, component_origin):
    """Accept opens Census changes' dialog, posts there, and the tab is
    redrawn where it is: the row now To enter by hand, Accept selected and
    focused, the notice said, nothing above the row moved."""
    errors = watch(page)
    page.set_viewport_size({"width": 1280, "height": 720})
    page.goto(component_origin + HUB_ACTIONS)
    page.evaluate(MARK)
    sent = answer_decisions(page, component_origin)
    row = home(page)
    visible(row.locator(".stage-chip", has_text="To review"))
    before = row.bounding_box()
    scrolled = page.evaluate("window.scrollY")
    accept = row.get_by_role("button", name="Accept Home address for Family")
    has_attribute(accept, "aria-pressed", "false")
    accept.click()
    dialog = page.locator("#hub-family-census-decide-dialog")
    visible(dialog)
    # The census page's own words and note, from the same shared dialog.
    has_text(dialog.locator("h2"), "Accept Home address for Family?")
    visible(dialog.get_by_label("Note (optional)"))
    dialog.get_by_role("button", name="Record").click()
    hidden(dialog)
    row = home(page)
    has_text(row.locator(".stage-chip .status-word"), "To enter by hand")
    # Pressed, it is the row's undo and its name says so (#2131).
    accept = row.locator("[data-row-action='accept']")
    has_attribute(accept, "aria-pressed", "true")
    has_attribute(
        accept,
        "aria-label",
        "Accepted; select to send Home address for Family back to review",
    )
    focused(accept)
    # The answer's notice is said in the page's status line.
    has_text(page.locator("main > div[role=status].visually-hidden"), NOTICE)
    body = sent[-1]
    assert "decide=accept" in body and "stage=all" in body
    assert "item=11111111-0000-4000-8000-000000000001" in body
    # No reload, no jump, and the row stayed where it was.
    assert page.evaluate(MARKED) == "kept"
    assert page.url.endswith(HUB_ACTIONS)
    assert page.evaluate("window.scrollY") == scrolled
    assert row.bounding_box()["y"] == pytest.approx(before["y"], abs=0.5)
    # Nothing was typed, so no "not kept" line: the dialog's note going back
    # to empty as it opened is no edit, though the redraw drew its form anew.
    assert page.locator("[data-edits-lost]").count() == 0
    assert_clean(page, errors)


def test_edit_opens_the_census_value_editor(page, component_origin):
    """Edit opens the row's own Census changes editor, Save waiting for a
    complete value; Cancel changes nothing."""
    page.goto(component_origin + HUB_ACTIONS)
    home(page).get_by_role("button", name="Edit Home address for Family").click()
    editor = page.locator(
        "#hub-family-census-edit-dialog-11111111-0000-4000-8000-000000000001"
    )
    visible(editor)
    assert editor.get_by_label("Street", exact=True).input_value() == "9 Census Way"
    editor.get_by_role("button", name="Cancel").click()
    hidden(editor)
    has_text(home(page).locator(".stage-chip .status-word"), "To review")


@pytest.mark.parametrize("path", [HUB_ACTIONS, HUB_ACCEPTED])
def test_the_acting_tab_is_accessible_and_fits_a_phone(
    page, component_origin, axe_source, path
):
    """axe finds nothing on the tab with its Actions column, open dialog
    closed; no sideways page scroll at phone width."""
    errors = watch(page)
    page.goto(component_origin + path)
    visible(home(page).locator("[data-row-action='accept']"))
    page.evaluate(axe_source)
    assert page.evaluate(AXE) == []
    page.set_viewport_size({"width": 320, "height": 700})
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert_clean(page, errors)


def test_an_edited_pledge_shows_its_reviewers_value_in_a_column(
    page, component_origin, axe_source
):
    """The pledge's table has the census Changed table's columns: the Edit's
    amount and frequency in Reviewer's value beside the fields they
    replace, and the pledge's one stage and actions spanning its rows; no
    text line above the table. axe finds nothing; it fits a phone."""
    errors = watch(page)
    page.goto(component_origin + HUB_PLEDGE)
    panel = page.locator("#family-tabs-panel-financial")
    table = panel.get_by_role("table", name="The Family's pledge")
    headings = table.locator("thead th").all_inner_texts()
    assert headings == [
        "Field",
        "Baseline",
        "Family's answer",
        "Reviewer's value",
        "Stage",
        "Actions",
    ]
    annual = table.get_by_role("row", name="Annual pledge (ParishSoft 2026)")
    visible(annual.get_by_role("cell", name="$1,300.00", exact=True))
    visible(annual.locator(".stage-chip"))
    # The pressed Accept is its Back to review (#2131).
    visible(annual.locator("button[data-row-undo]"))
    frequency = table.get_by_role("row", name="Frequency")
    visible(frequency.get_by_role("cell", name="Monthly ($108.33 each)", exact=True))
    assert panel.get_by_text("Reviewer's value:").count() == 0
    assert panel.get_by_text("This pledge:").count() == 0
    page.evaluate(axe_source)
    assert page.evaluate(AXE) == []
    page.set_viewport_size({"width": 320, "height": 700})
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert_clean(page, errors)


def test_accept_on_the_pledge_returns_focus_to_its_icon(page, component_origin):
    """Accept on the pledge posts to Pledges, and the redrawn tab (here the
    pledge decided and edited) puts focus back on Accept, now selected, in
    the pledge table's first row, which its spanning Actions cell belongs
    to; nothing reloads."""
    errors = watch(page)
    page.goto(component_origin + HUB_PLEDGE_FRESH)
    page.evaluate(MARK)
    sent = []

    def pledges(route):
        """Keep the decision's body; answer as the page does, with a notice."""
        sent.append(route.request.post_data)
        route.fulfill(
            content_type="text/html",
            body="<!doctype html><p data-confirm-announce>Pledge accepted.</p>",
        )

    page.route("**" + PLEDGES, pledges)
    page.route(
        lambda url: url.endswith("?tab=financial"),
        lambda route: route.fulfill(
            response=route.fetch(url=component_origin + HUB_PLEDGE, method="GET")
        ),
    )
    table = page.locator("#family-tabs-panel-financial").get_by_role(
        "table", name="The Family's pledge"
    )
    table.locator("[data-row-action='accept']").click()
    dialog = page.locator("#hub-financial-pledges-decide-dialog")
    visible(dialog)
    dialog.get_by_role("button", name="Record").click()
    hidden(dialog)
    accept = table.locator("tbody tr").first.locator("[data-row-action='accept']")
    has_attribute(accept, "aria-pressed", "true")
    focused(accept)
    assert "decide=accept" in sent[-1]
    assert page.evaluate(MARKED) == "kept"
    assert_clean(page, errors)


@pytest.mark.parametrize("redrawn", [HUB_PLEDGE, HUB_PLEDGE_FRESH])
def test_a_saved_pledge_edit_shows_no_not_kept_line(page, component_origin, redrawn):
    """Edit's Save posts what was typed, and the tab redraws itself from its
    address: the pledge edited (no Edit form any more: regression cover), or
    still To review, its row's Edit form then drawn anew (the case the line
    was wrongly shown for). Either way no "Changes you typed ... were not
    kept" line, since what was typed was saved (#2123)."""
    errors = watch(page)
    page.goto(component_origin + HUB_PLEDGE_FRESH)
    page.evaluate(MARK)
    sent = []

    def pledges(route):
        """Keep the decision's body; answer as the page does, with a notice."""
        sent.append(route.request.post_data)
        route.fulfill(
            content_type="text/html",
            body="<!doctype html><p data-confirm-announce>Pledge edited.</p>",
        )

    page.route("**" + PLEDGES, pledges)
    page.route(
        lambda url: url.endswith("?tab=financial"),
        lambda route: route.fulfill(
            response=route.fetch(url=component_origin + redrawn, method="GET")
        ),
    )
    table = page.locator("#family-tabs-panel-financial").get_by_role(
        "table", name="The Family's pledge"
    )
    table.locator("[data-row-action='edit']").click()
    editor = page.locator("dialog[open]")
    visible(editor)
    editor.get_by_label("Annual pledge in dollars").fill("1,300")
    editor.get_by_label("Frequency").select_option("monthly")
    editor.get_by_role("button", name="Save").click()
    hidden(editor)
    has_text(page.locator("main > div[role=status].visually-hidden"), "Pledge edited.")
    assert "decide=edit" in sent[-1] and "value_amount=1%2C300" in sent[-1]
    assert page.evaluate(MARKED) == "kept"
    assert page.locator("[data-edits-lost]").count() == 0
    assert_clean(page, errors)
