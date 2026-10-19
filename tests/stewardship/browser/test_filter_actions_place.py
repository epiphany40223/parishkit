"""Apply filters and Clear filters take the least room above the table (#1776).

The ui-conventions spec, "Clear filters": the pair sits by the first of
three steps that fits, "beside" (side by side on the fields' last row),
"stacked" (Clear under Apply on that row, which must keep its height) or
"row" (side by side on a row of the form's own). The test is its own
oracle: for each page and width it measures the form with the pair taken
out, then tries each step on the pair, and checks that the step the page
chose is the first that keeps every field's line and the form's height,
so no row is added while an earlier step fits. Financial stewardship is
a Filters panel grid, the Family directory a filter bar in a panel, Sent
mail and the Timeline one-row filter bars, and Census changes and the
review worklist's fixture (#1681) worklists, wrapping rows of fixed-width
fields. Run in the Linux Playwright image too: its
DejaVu fonts are wider (Sent mail's bar did not fit at 1280 px there, t32).
"""

import pytest

from .census_components import PATH as CENSUS
from .review_worklist_components import PATH as WORKLIST
from .timeline_components import ADMIN as TIMELINE
from .waits import eventually

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

PAGES = [
    CENSUS,
    "/financial-report",
    "/family-directory",
    "/deliveries-names",
    TIMELINE,
    WORKLIST,
]
WIDTHS = [390, 768, 1280, 1440]
STEPS = ("beside", "stacked", "row")
PAIR = "#table-filters .filter-actions"
# Draw the pair in one step (in a grid, "wide" spans two cells), or take
# it out of the layout (null), and measure the form: its height, every
# field's top and bottom (the pair's siblings before it that draw a box,
# hint lines aside), the pair's own edges, whether both buttons share a
# line, whether the pair overflows and whether its box is a grid of more
# than one column.
MEASURE = """([selector, step, wide]) => {
  const pair = document.querySelector(selector);
  pair.style.display = step === null ? "none" : "";
  if (step !== null) pair.dataset.filterPlace = step;
  pair.toggleAttribute("data-filter-wide", Boolean(wide));
  const form = pair.closest("form");
  const box = getComputedStyle(pair.parentElement);
  const fields = [];
  const hint = "p, [data-complete-hint], .field-error-line";
  let node = pair.previousElementSibling;
  for (; node; node = node.previousElementSibling) {
    if (node.getClientRects().length && !node.matches(hint)) fields.push(node);
  }
  const edges = (node) => {
    const box = node.getBoundingClientRect();
    return [box.top + scrollY, box.bottom + scrollY];
  };
  const [apply, clear] = pair.querySelectorAll(
    ":scope > button, :scope > .filter-clear");
  const top = (node) => node.getBoundingClientRect().top;
  return {
    form: form.getBoundingClientRect().height,
    fields: fields.map(edges),
    pair: step === null ? null : edges(pair),
    oneLine: step === null ? null : Math.abs(top(apply) - top(clear)) < 1,
    overflows: step !== null && pair.scrollWidth > pair.clientWidth + 1,
    grid: box.display === "grid"
      && box.gridTemplateColumns.trim().split(/\\s+/).length > 1,
  };
}"""


def fits(step, shown, alone):
    """Whether ``step`` keeps the fields' lines and the form's height."""
    if shown["overflows"] or (step == "beside" and not shown["oneLine"]):
        return False
    if abs(shown["form"] - alone["form"]) > 1:
        return False
    if any(
        abs(a - b) > 1
        for now, then in zip(shown["fields"], alone["fields"], strict=True)
        for a, b in zip(now, then, strict=True)
    ):
        return False
    # The pair sits within the fields' last row (its line's band).
    last_top, last_bottom = alone["fields"][0]
    row = [
        (top, bottom)
        for top, bottom in alone["fields"]
        if top < last_bottom - 1 and bottom > last_top + 1
    ]
    top, bottom = shown["pair"]
    return top >= min(t for t, _ in row) - 1 and bottom <= max(b for _, b in row) + 1


@pytest.mark.parametrize("width", WIDTHS)
@pytest.mark.parametrize("path", PAGES)
def test_the_pair_takes_the_first_step_that_fits(page, component_origin, path, width):
    """The page's step is the first that adds no row, and a new row is
    used only when neither beside step fits; the page never scrolls sideways."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + path)
    page.evaluate("document.fonts.ready.then(() => true)")
    # A closed Filters panel is opened: the pair is placed when it is shown.
    summary = page.locator("details[data-filter-panel]:not([open]) > summary")
    if summary.count():
        summary.click()
    pair = page.locator(PAIR)
    chosen = (
        pair.get_attribute("data-filter-place"),
        pair.get_attribute("data-filter-wide") is not None,
    )
    assert chosen[0] in STEPS, (path, width, chosen)
    assert page.evaluate(
        "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
    )
    alone = page.evaluate(MEASURE, [PAIR, None, False])
    # Step 1 in one cell, then (a grid of several columns only) in two.
    order = [("beside", False)]
    if alone["grid"]:
        order.append(("beside", True))
    order += [("stacked", False), ("row", False)]
    tried = {step: page.evaluate(MEASURE, [PAIR, *step]) for step in order}
    page.evaluate(MEASURE, [PAIR, *chosen])
    expected = next(
        (step for step in order[:-1] if fits(step[0], tried[step], alone)),
        ("row", False),
    )
    assert chosen == expected, (path, width, chosen, tried, alone)
    if chosen[0] == "row":
        # A row of its own at the form's foot, below every field, both
        # buttons side by side (both fit any of these widths).
        shown = tried[("row", False)]
        assert shown["pair"][0] >= max(bottom for _, bottom in alone["fields"]) - 1
        assert shown["oneLine"], (path, width, shown)
    # Each button is inside the screen.
    assert page.evaluate(
        """(selector) => [...document.querySelectorAll(selector + ' button')]
            .every((node) => node.getBoundingClientRect().right
                <= document.documentElement.clientWidth)""",
        PAIR,
    )


def test_a_resize_places_the_pair_again(page, component_origin):
    """Sent mail at a wide screen and then a phone: the pair's step follows
    the width, with no reload."""
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(component_origin + "/deliveries-names")
    page.evaluate("document.fonts.ready.then(() => true)")
    pair = page.locator(PAIR)
    wide = pair.get_attribute("data-filter-place")
    page.set_viewport_size({"width": 390, "height": 900})
    eventually(
        page,
        "(selector) => document.querySelector(selector).dataset.filterPlace === 'row'",
        arg=PAIR,
    )
    assert wide in STEPS
