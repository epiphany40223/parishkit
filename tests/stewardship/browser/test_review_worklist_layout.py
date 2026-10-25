"""The review work tables' layout (UX conventions, "Review work tables",
#2124), on every stream's page: Census changes, Ministry requests (and its
To enter by hand view), Pledges, Additional information and Limitations.

At 1366, 1280, 900 and 320 px nothing scrolls sideways, neither the page
nor the table's own box, and no name, field or stage breaks a word across
lines; where the table's box is at least 57rem wide the key columns keep
their minimum widths, and below that each row is a card of labelled
values. A long value is clamped to two lines with Show all, whose bubble
opens over the page without moving any control. A 1280x800 window with
the Admin menu open keeps the table layout. In Chromium and WebKit.
"""

import pytest

from .census_components import PATH as CENSUS
from .followup_components import ENTRY, REQUESTS
from .information_components import PATH as INFORMATION
from .limitations_components import PATH as LIMITATIONS
from .pledges_components import PATH as PLEDGES
from .test_charts import assert_clean, watch
from .waits import focused, hidden, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "webkit"], indirect=True
)
PAGES = (CENSUS, REQUESTS, ENTRY, PLEDGES, INFORMATION, LIMITATIONS)
WIDTHS = (1366, 1280, 900, 320)
# The card layout's threshold: the table's box narrower than 57rem.
CARDS_BELOW = 57 * 16
# Each key column's cells and their minimum width in px (ui-v1.css): the
# names (the row header and the other name columns), fields and stages.
MINIMUMS = {
    "tbody th[scope=row], tbody .name-cell": 7 * 16,
    "tbody .field-cell": 6 * 16,
    "tbody .stage-cell": 6.5 * 16,
}
FIT = """() => {
  const box = document.querySelector('.worklist-table .table-scroll');
  return {page: document.documentElement.scrollWidth <= innerWidth,
          box: box.scrollWidth <= box.clientWidth + 1,
          width: box.clientWidth};
}"""
# Each word of a name, field or stage cell that the browser broke across
# two lines (a Range over it has more than one line box).
BROKEN = """(selectors) => {
  const broken = [];
  const cells = selectors.flatMap((selector) => selector.split(', '))
    .map((selector) => `.review-table ${selector}`).join(', ');
  for (const cell of document.querySelectorAll(cells)) {
    const walker = document.createTreeWalker(cell, NodeFilter.SHOW_TEXT);
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      if (node.parentElement.closest('.visually-hidden')) continue;
      for (const match of node.textContent.matchAll(/\\S+/g)) {
        const range = document.createRange();
        range.setStart(node, match.index);
        range.setEnd(node, match.index + match[0].length);
        const lines = new Set([...range.getClientRects()].map(
          (rect) => Math.round(rect.top)));
        if (lines.size > 1) broken.push(match[0]);
      }
    }
  }
  return broken;
}"""
# The narrowest of each key column's cells.
WIDTHS_OF = """(selectors) => Object.fromEntries(selectors.map((selector) => [
  selector, Math.min(...[...document.querySelectorAll(
    selector.split(', ').map((part) => `.review-table ${part}`).join(', '))]
    .map((cell) => cell.getBoundingClientRect().width))]))"""
# How the first row is drawn, and whether its cells show their labels.
ROW = """() => {
  const row = document.querySelector('.review-table tbody tr');
  const labelled = [...row.querySelectorAll('[data-label]:not(.review-actions)')].map(
    (cell) => getComputedStyle(cell, '::before').content);
  return {display: getComputedStyle(row).display, labelled,
          headings: [...document.querySelectorAll('.review-table thead th')]
            .filter((th) => getComputedStyle(th).display !== 'none').length};
}"""
# Each Show all whose showing disagrees with whether its value is cut
# short by the clamp.
MISPLACED = """() => [...document.querySelectorAll('.review-table .clamped')]
  .filter((cell) => cell.querySelector('.cell-more'))
  .filter((cell) => {
    const value = cell.querySelector('.value-clamp');
    const cut = value.scrollHeight > value.clientHeight + 1;
    const shown = getComputedStyle(cell.querySelector('.cell-more-button'))
      .visibility === 'visible';
    return cut !== shown;
  }).map((cell) => cell.textContent)"""
# Every control in the worklist region, with its box.
CONTROLS = """() => [...document.querySelectorAll(
  '.worklist-table button, .worklist-table input, .worklist-table a')].map((node) => {
  const box = node.getBoundingClientRect();
  return [box.left, box.top, box.width, box.height];
})"""


@pytest.mark.parametrize("width", WIDTHS)
def test_the_tables_fit_without_breaking_words(page, component_origin, width):
    """Nothing scrolls sideways and no name, field or stage word breaks
    across lines; in the table layout each key column keeps its minimum
    width, and in the card layout every cell is labelled."""
    page.set_viewport_size({"width": width, "height": 900})
    errors = watch(page)
    for path in PAGES:
        page.goto(component_origin + path)
        page.evaluate("document.fonts.ready")
        fit = page.evaluate(FIT)
        assert fit["page"] and fit["box"], (path, width, fit)
        assert page.evaluate(BROKEN, list(MINIMUMS)) == [], (path, width)
        assert page.evaluate(MISPLACED) == [], (path, width)
        row = page.evaluate(ROW)
        if fit["width"] >= CARDS_BELOW:
            assert row["display"] == "table-row", (path, width)
            present = [
                selector
                for selector in MINIMUMS
                if page.locator(f".review-table :is({selector})").count()
            ]
            for name, narrowest in page.evaluate(WIDTHS_OF, present).items():
                assert narrowest >= MINIMUMS[name] - 0.5, (path, width, name)
        else:
            assert row["display"] == "flex", (path, width)
            assert row["labelled"] and all(
                label not in ("none", "normal", "") for label in row["labelled"]
            ), (path, width, row)
            # Only the selection box and the sort controls stay above the cards.
            assert row["headings"] < page.locator(".review-table thead th").count()
    assert_clean(page, errors)


# At a laptop window: the Admin menu's right edge, the table's box, the
# table's narrowest table-layout width (min-content, measured by sizing it
# so for a moment) and how its first row is drawn.
LAPTOP = """() => {
  const menu = document.querySelector('nav.admin-sidebar').getBoundingClientRect();
  const box = document.querySelector('.worklist-table .table-scroll');
  const table = box.querySelector('table');
  table.style.width = 'min-content';
  const narrowest = table.getBoundingClientRect().width;
  table.style.width = '';
  return {menu: menu.width && menu.right, left: box.getBoundingClientRect().left,
          box: box.clientWidth, narrowest,
          display: getComputedStyle(table.querySelector('tbody tr')).display};
}"""


def test_a_1280_laptop_window_keeps_the_table_beside_the_menu(page, component_origin):
    """At a 1280x800 window with the Admin menu open beside the content,
    every review table keeps its table layout (#2124 asked for cards only
    below about 900 px) and fits its box: a build with a 60rem breakpoint,
    above the 927/942 px box (Chromium/WebKit), showed cards here."""
    page.set_viewport_size({"width": 1280, "height": 800})
    for path in PAGES:
        page.goto(component_origin + path)
        page.evaluate("document.fonts.ready")
        laptop = page.evaluate(LAPTOP)
        assert 0 < laptop["menu"] <= laptop["left"], (path, laptop)
        assert laptop["box"] >= CARDS_BELOW, (path, laptop)
        assert laptop["display"] == "table-row", (path, laptop)
        assert laptop["narrowest"] <= laptop["box"], (path, laptop)


@pytest.mark.parametrize("width", (1366, 320))
def test_show_all_opens_a_long_value_in_place(page, component_origin, width):
    """A long value (a Family address) is clamped with Show all; opening it
    shows the whole value in a bubble beside the button and moves no
    control; Escape closes it and puts focus back on Show all."""
    page.set_viewport_size({"width": width, "height": 900})
    errors = watch(page)
    page.goto(component_origin + CENSUS)
    button = page.locator(".review-table .cell-more-button").first
    button.scroll_into_view_if_needed()
    bubble = page.locator("#" + button.get_attribute("aria-controls"))
    value = button.locator("xpath=ancestor::span[@class='clamped']/span[1]")
    # The clamp keeps the value to two lines.
    lines = value.evaluate(
        "node => node.getBoundingClientRect().height"
        " / parseFloat(getComputedStyle(node).lineHeight)"
    )
    assert lines <= 2.05
    before = page.evaluate(CONTROLS)
    # From the keyboard, since WebKit does not focus a clicked button.
    button.focus()
    page.keyboard.press("Enter")
    visible(bubble)
    assert button.get_attribute("aria-expanded") == "true"
    assert bubble.inner_text().strip() == value.inner_text().strip()
    assert page.evaluate(CONTROLS) == before
    near = button.bounding_box()
    box = bubble.bounding_box()
    assert abs(box["y"] - (near["y"] + near["height"])) < 12 or box["y"] < near["y"]
    page.keyboard.press("Escape")
    hidden(bubble)
    focused(button)
    assert page.evaluate(CONTROLS) == before
    assert_clean(page, errors)


def test_a_clamped_value_shows_whole_on_hover(page, component_origin):
    """The pointer resting on a long value its cell clamps (a Family
    address) shows it whole in the shared tip, as every cut-short value in
    a table does; the value adds no tab stop, Show all being the keyboard's
    way to it, and leaving it hides the tip (UX conventions, "Table fit and
    row height", cut-short values, #2124)."""
    from playwright.sync_api import expect

    page.set_viewport_size({"width": 1366, "height": 900})
    page.goto(component_origin + CENSUS)
    button = page.locator(".review-table .cell-more-button").first
    button.scroll_into_view_if_needed()
    value = button.locator("xpath=ancestor::span[@class='clamped']/span[1]")
    assert value.evaluate("e => e.scrollHeight > e.clientHeight + 1")
    assert value.get_attribute("tabindex") is None
    value.hover()
    tip = page.locator(".admin-tip.is-shown")
    expect(tip).to_have_text(value.text_content().strip())
    page.mouse.move(5, 5, steps=4)
    expect(tip).to_have_count(0)
