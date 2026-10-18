"""Census changes' table keeps readable columns at every width (#1472).

In Chromium and WebKit. The real page is served by ``census_components``
with realistic long values (``WIDE``): a long Family name, a Family address,
a New Member, an edited Conflict and a recorded change. At each width the
page itself never scrolls sideways; the table may scroll inside its own box
(UX conventions, "Table fit and row height"). No column is squeezed into a
strip: each keeps a minimum width, the labelled Values and Last recorded
lists keep a readable one, and no row grows into a column of single
letters. The Actions column stays in view at the box's end however far the
box is scrolled, so a row's actions never scroll out of reach.
"""

import pytest

from .census_components import WIDE

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "webkit"], indirect=True
)

WIDTHS = [320, 400, 641, 700, 1024, 1280]

# What the test reads from the drawn page, in CSS pixels: the page's and the
# table box's scroll widths, each heading's width by its text, each labelled
# value's width, the tallest row, and the box and Actions cells' edges.
MEASURE = """() => {
  const box = document.querySelector('#table .table-scroll');
  const edges = box.getBoundingClientRect();
  const heads = {};
  for (const th of box.querySelectorAll('thead th')) {
    const link = th.querySelector('.sort-link');
    const text = link ? link.firstChild.textContent : th.textContent;
    heads[text.trim()] = th.getBoundingClientRect().width;
  }
  const actions = [...box.querySelectorAll('tr > :last-child')].map(cell => {
    const r = cell.getBoundingClientRect();
    return [r.left, r.right];
  });
  return {
    page: [document.documentElement.scrollWidth, document.documentElement.clientWidth],
    box: [box.scrollWidth, box.clientWidth, edges.left, edges.right],
    heads,
    values: [...box.querySelectorAll('dl.cell-pairs-wrap')].map(
      dl => dl.getBoundingClientRect().width),
    tallest: Math.max(...[...box.querySelectorAll('tbody tr')].map(
      row => row.getBoundingClientRect().height)),
    actions,
    icons: box.querySelectorAll('.table-actions .icon-button').length,
  };
}"""


def check(seen):
    """Assert one measurement: no page scroll, readable columns, and the
    Actions column inside the table's box."""
    width, viewport = seen["page"]
    assert width <= viewport, f"the page scrolls sideways: {width} > {viewport}"
    # Every column keeps a usable width; the labelled lists a readable one.
    assert min(seen["heads"].values()) >= 64, seen["heads"]
    assert seen["heads"]["Values"] >= 176 and seen["heads"]["Last recorded"] >= 176
    assert seen["values"] and min(seen["values"]) >= 176, seen["values"]
    # Before #1472 a squeezed row was over 1,500 px tall.
    assert seen["tallest"] < 420, seen["tallest"]
    # Every row's Actions cell (and the heading's) sits inside the box.
    _, _, left, right = seen["box"]
    for start, end in seen["actions"]:
        assert left - 1 <= start and end <= right + 1, (start, end, left, right)
    assert seen["icons"] >= 8


@pytest.mark.parametrize("width", WIDTHS)
def test_census_table_columns_stay_readable(page, component_origin, width):
    """At each width the page never scrolls sideways, the columns keep their
    minimum widths, and Actions stays in view at the start and the end of
    the table box's own scroll."""
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.set_viewport_size({"width": width, "height": 800})
    page.goto(component_origin + WIDE)
    box = page.locator("#table .table-scroll")
    assert page.locator("#table tbody tr").count() == 4
    check(page.evaluate(MEASURE))
    # The table is wider than its box at these widths, so the box scrolls;
    # scrolled to its end, Actions is still where it was.
    assert box.evaluate("box => box.scrollWidth > box.clientWidth")
    before = page.evaluate(MEASURE)["actions"]
    box.evaluate("box => { box.scrollLeft = box.scrollWidth; }")
    after = page.evaluate(MEASURE)
    check(after)
    # Sticky cells may land on another sub-pixel, never further.
    moved = [
        abs(was - now)
        for edges in zip(before, after["actions"], strict=True)
        for was, now in zip(*edges, strict=True)
    ]
    assert max(moved) <= 1, moved
    # The first row's actions answer to the pointer where they are drawn.
    first = page.locator("#table tbody tr").first.locator(".icon-button").first
    first.hover()
    assert first.evaluate("button => button.matches(':hover')")
    assert errors == []
