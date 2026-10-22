"""The review worklist's compact top (#1653), on every page built on it.

Its filters sit on one row at laptop width, so with the Testing banner
shown the first table row starts inside a 1366×768 window on Census
changes, Additional information, Pledges and Ministry requests (where it
started about 1,037 px down). Ministry requests' By Ministry counts are a
closed disclosure on the heading's row, remembered open per browser
(remember-open-v1.js) without moving the page after it shows, and
opening About this page, by hand or remembered, never moves its
control. At phone widths the fields stack with no sideways scroll, and
the dark scheme passes axe's contrast checks. In Chromium and WebKit.
"""

import pytest

from .census_components import PATH as CENSUS
from .followup_components import ENTRY, REQUESTS
from .information_components import PATH as INFORMATION
from .pledges_components import PATH as PLEDGES
from .test_charts import assert_clean, watch
from .waits import eventually

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "webkit"], indirect=True
)
PAGES = (CENSUS, INFORMATION, PLEDGES, REQUESTS, ENTRY)
AXE = """async () => (await axe.run(document, {
  runOnly: {type: 'tag', values: ['wcag2a','wcag2aa','wcag21aa','wcag22aa']}
})).violations.map(({id,impact}) => ({id,impact}))"""
# Each field cell of the filter form (not the buttons' pair), with its box.
CELLS = """() => [...document.querySelectorAll(
  '#table-filters > div:not(.filter-actions)')].map((cell) => {
  const box = cell.getBoundingClientRect();
  return {left: box.left, right: box.right, bottom: box.bottom};
})"""
FIRST_ROW = "#table tbody tr"
SUMMARY = "details.by-ministry > summary"
KEY = "pk-remember-open:ministry-requests-by-ministry"


def top(page, selector):
    """The first match's top edge on the page, scroll included."""
    return page.locator(selector).first.evaluate(
        "node => node.getBoundingClientRect().top + scrollY"
    )


def no_sideways_scroll(page):
    """The page is no wider than the window."""
    return page.evaluate("document.documentElement.scrollWidth <= innerWidth")


def test_the_first_row_starts_on_a_laptop_screen(page, component_origin):
    """At 1366×768, with the Testing banner and every About and By Ministry
    disclosure closed, each worklist's filter fields share one row with
    Apply filters and Clear filters, and its first row starts inside the
    window."""
    page.set_viewport_size({"width": 1366, "height": 768})
    for path in PAGES:
        page.goto(component_origin + path)
        page.evaluate("document.fonts.ready")
        bottoms = {round(cell["bottom"]) for cell in page.evaluate(CELLS)}
        assert max(bottoms) - min(bottoms) <= 1, (path, bottoms)
        pair = page.locator("#table-filters .filter-actions")
        assert pair.get_attribute("data-filter-place") in ("beside", "stacked"), path
        assert top(page, FIRST_ROW) < 768, (path, top(page, FIRST_ROW))
        assert no_sideways_scroll(page), path


@pytest.mark.parametrize("width", [390, 320])
def test_narrow_windows_stack_the_fields(page, component_origin, width):
    """On a phone each filter field takes the form's whole width, one
    under another, and nothing scrolls sideways."""
    page.set_viewport_size({"width": width, "height": 768})
    for path in PAGES:
        page.goto(component_origin + path)
        cells = page.evaluate(CELLS)
        lefts = {round(cell["left"]) for cell in cells}
        rights = {round(cell["right"]) for cell in cells}
        assert len(lefts) == 1 and len(rights) == 1, (path, cells)
        assert len({round(cell["bottom"]) for cell in cells}) == len(cells), path
        assert no_sideways_scroll(page), path


def test_by_ministry_opens_in_place_on_the_heading_line(page, component_origin):
    """By Ministry starts closed, its control on the heading's line after
    About this page. Opening it shows the counts below that line without
    moving the heading, either control or the filters' row, which only
    moves down by the counts' height; closing it puts everything back."""
    page.set_viewport_size({"width": 1366, "height": 768})
    errors = watch(page)
    page.goto(component_origin + REQUESTS)
    summary = page.locator(SUMMARY)
    section = page.locator("#by-ministry")
    assert not section.is_visible()
    heading = page.locator("h1").bounding_box()
    about = page.locator("details.about-page > summary").bounding_box()
    closed = summary.bounding_box()
    # On the heading's line, to the right of About this page.
    assert closed["x"] >= about["x"] + about["width"]
    assert closed["y"] < heading["y"] + heading["height"]
    assert closed["y"] + closed["height"] > heading["y"]
    cells = page.evaluate(CELLS)
    filters = top(page, "#table-filters")
    summary.click()
    assert section.is_visible()
    assert summary.get_attribute("aria-expanded") in (None, "true")
    assert summary.bounding_box() == closed
    assert page.locator("details.about-page > summary").bounding_box() == about
    shifted = top(page, "#table-filters") - filters
    counts = section.bounding_box()
    # The counts sit below the heading's line and above the filters, which
    # move down by the counts' room and keep their row as it was.
    assert counts["y"] >= heading["y"] + heading["height"] - 1
    assert counts["y"] + counts["height"] <= top(page, "#table-filters")
    moved = page.evaluate(CELLS)
    assert [cell["left"] for cell in moved] == [cell["left"] for cell in cells]
    assert all(
        abs(after["bottom"] - before["bottom"] - shifted) < 1
        for before, after in zip(cells, moved, strict=True)
    )
    summary.click()
    assert not section.is_visible()
    assert top(page, "#table-filters") == filters
    assert summary.bounding_box() == closed
    assert_clean(page, errors)


def test_by_ministry_is_remembered_open_before_the_page_shows(page, component_origin):
    """Opened once, By Ministry is open on the next visit, already when the
    filters that follow it are parsed, so nothing moves after the page
    shows. Closing it forgets it; the page stores nothing else."""
    page.set_viewport_size({"width": 1366, "height": 768})
    # Record whether By Ministry was open when the filter form was parsed.
    page.add_init_script(
        """new MutationObserver((records, observer) => {
          const form = document.getElementById('table-filters');
          if (!form) return;
          window.__openWhenParsed = document.querySelector('details.by-ministry').open;
          observer.disconnect();
        }).observe(document, {childList: true, subtree: true});"""
    )
    errors = watch(page)
    page.goto(component_origin + REQUESTS)
    assert page.evaluate("window.__openWhenParsed") is False
    page.locator(SUMMARY).click()
    # The toggle event that stores it is a task of its own.
    eventually(page, f"localStorage.getItem('{KEY}')", "open")
    page.reload()
    assert page.evaluate("window.__openWhenParsed") is True
    assert page.locator("#by-ministry").is_visible()
    page.locator(SUMMARY).click()
    eventually(page, f"localStorage.getItem('{KEY}')", None)
    page.reload()
    assert page.evaluate("window.__openWhenParsed") is False
    assert_clean(page, errors)


ABOUT = "details.about-page > summary"
ABOUT_KEY = "pk-about-page:ministry-followup"
# A selector's box on the page (scroll included), rounded to whole pixels.
BOX = """(selector) => {
  const box = document.querySelector(selector).getBoundingClientRect();
  return [box.left, box.top + scrollY, box.width, box.height].map(Math.round);
}"""


@pytest.mark.parametrize("width", [1366, 1024, 390, 320])
def test_opening_about_keeps_by_ministry_in_place(page, component_origin, width):
    """The heading and both controls share one row (the controls a second
    one on a phone), and each disclosure's content takes a full-width row
    below them, About's first. Opening or closing either one, alone or
    with the other open, moves neither control; only what follows the
    controls' row moves."""
    page.set_viewport_size({"width": width, "height": 768})
    errors = watch(page)
    page.goto(component_origin + REQUESTS)
    page.evaluate("localStorage.clear()")
    page.goto(component_origin + REQUESTS)
    about, by_ministry = page.evaluate(BOX, ABOUT), page.evaluate(BOX, SUMMARY)
    controls_foot = max(about[1] + about[3], by_ministry[1] + by_ministry[3])

    def unmoved():
        """Neither control has moved, and the page does not scroll sideways."""
        assert page.evaluate(BOX, ABOUT) == about
        assert page.evaluate(BOX, SUMMARY) == by_ministry
        assert no_sideways_scroll(page)

    page.locator(ABOUT).click()
    assert page.locator(".about-page-body").is_visible()
    unmoved()
    help_box = page.evaluate(BOX, ".about-page-body")
    assert help_box[1] >= controls_foot - 1  # rounding
    page.locator(SUMMARY).click()
    assert page.locator("#by-ministry").is_visible()
    unmoved()
    counts = page.evaluate(BOX, "#by-ministry")
    # Both open: About's help, then the counts, then the filters.
    assert page.evaluate(BOX, ".about-page-body") == help_box
    assert counts[1] >= help_box[1] + help_box[3] - 1
    assert top(page, "#table-filters") >= counts[1] + counts[3] - 1
    page.locator(ABOUT).click()
    assert not page.locator(".about-page-body").is_visible()
    unmoved()
    assert page.evaluate(BOX, "#by-ministry")[1] >= controls_foot - 1
    page.locator(SUMMARY).click()
    unmoved()
    assert_clean(page, errors)


@pytest.mark.parametrize("by_ministry_open", [False, True])
def test_a_remembered_about_does_not_move_by_ministry_on_load(
    page, component_origin, by_ministry_open
):
    """ui-v1.js reopens a remembered About this page after the page first
    shows; By Ministry's control, open or closed, is where it was before
    About opened."""
    page.set_viewport_size({"width": 1366, "height": 768})
    page.goto(component_origin + REQUESTS)
    page.evaluate(
        "([about, key, open]) => { localStorage.clear();"
        " localStorage.setItem(about, 'open');"
        " if (open) localStorage.setItem(key, 'open'); }",
        [ABOUT_KEY, KEY, by_ministry_open],
    )
    # Record By Ministry's box as soon as the filters are parsed, before
    # the deferred ui-v1.js opens About.
    page.add_init_script(
        "new MutationObserver((records, observer) => {"
        " if (!document.getElementById('table-filters')) return;"
        " window.__aboutOpenWhenParsed ="
        " document.querySelector('details.about-page').open;"
        f" window.__boxWhenParsed = ({BOX})('{SUMMARY}');"
        " observer.disconnect();"
        "}).observe(document, {childList: true, subtree: true});"
    )
    errors = watch(page)
    page.goto(component_origin + REQUESTS)
    page.wait_for_load_state("load")
    assert page.evaluate("window.__aboutOpenWhenParsed") is False
    assert page.locator(".about-page-body").is_visible()
    assert page.locator("#by-ministry").is_visible() is by_ministry_open
    assert page.evaluate(BOX, SUMMARY) == page.evaluate("window.__boxWhenParsed")
    assert_clean(page, errors)


def test_by_ministry_without_storage_starts_closed(page, component_origin):
    """With browser storage refused (a private window, blocked site data),
    By Ministry starts closed and still opens and closes, with no error."""
    page.add_init_script(
        """Object.defineProperty(window, 'localStorage', {get() {
          throw new DOMException('blocked', 'SecurityError'); }});"""
    )
    errors = watch(page)
    page.goto(component_origin + REQUESTS)
    section = page.locator("#by-ministry")
    assert not section.is_visible()
    page.locator(SUMMARY).click()
    assert section.is_visible()
    page.locator(SUMMARY).click()
    assert not section.is_visible()
    assert_clean(page, errors)


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_compact_top_passes_axe_in_both_schemes(
    page, component_origin, axe_source, scheme
):
    """Each page's compact top, with By Ministry open, has no WCAG
    violation (contrast included) in the light and the dark scheme, at a
    laptop and a phone width."""
    page.emulate_media(color_scheme=scheme)
    for width in (1366, 390):
        page.set_viewport_size({"width": width, "height": 768})
        for path in PAGES:
            page.goto(component_origin + path)
            assert page.evaluate("document.documentElement.dataset.theme") == scheme
            # Open (it may already be, remembered from the last visit).
            page.locator("details.by-ministry").evaluate_all(
                "panels => panels.forEach((panel) => { panel.open = true; })"
            )
            page.evaluate(axe_source)
            assert page.evaluate(AXE) == [], (width, path)
