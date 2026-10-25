"""The review worklist's first row on a laptop's first screen (#1950).

At 1366 × 768, with the Testing banner and every About and By Ministry
disclosure closed, the first table row of Census changes, Additional
information, Pledges and Ministry requests ends inside the window. The
room comes from the filter form's reserved hint sharing the notice's
reserved line (the hint shows there while the filters cannot be applied,
a decision's notice always wins the line, and nothing moves either way),
the bulk bar keeping one line
and the table region's tighter spacing. At 320 px nothing scrolls
sideways, and the shared line passes axe in the light and dark schemes.
In Chromium and WebKit.
"""

import pytest

from .census_components import PATH as CENSUS
from .followup_components import ENTRY, REQUESTS
from .information_components import PATH as INFORMATION
from .pledges_components import PATH as PLEDGES
from .review_worklist_components import ACCEPTED, ACCEPTED_NOTICE, PATH
from .test_charts import assert_clean, watch
from .test_review_worklist import icon, posts_to
from .test_review_worklist_compact import AXE, no_sideways_scroll
from .waits import eventually, has_text

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "webkit"], indirect=True
)
PAGES = (CENSUS, INFORMATION, PLEDGES, REQUESTS, ENTRY)
HINT = "#table-filters > [data-complete-hint]"
NOTICE = "#worklist-notice"
# The form's own reason while a filter is refused, and the hint emptied.
HELD = "Correct the filters to apply them."
CLEARED = f"() => document.querySelector('{HINT}').textContent === ''"
# The boxes the shared line tests compare, to the pixel, on the page (the
# table by its top alone: a decision redraws its rows).
BOXES = """() => {
  const box = (selector) => {
    const rect = document.querySelector(selector).getBoundingClientRect();
    return {top: Math.round(rect.top + scrollY), height: Math.round(rect.height)};
  };
  return {hint: box('#table-filters > [data-complete-hint]'),
          notice: box('#worklist-notice'), table: {top: box('#table').top},
          form: box('#table-filters')};
}"""
# Whether the notice's words, and the hint's, are drawn on the line.
NOTICE_SHOWN = (
    "() => getComputedStyle(document.querySelector('#worklist-notice > p'))"
    ".visibility === 'visible'"
)
HINT_SHOWN = (
    f"() => getComputedStyle(document.querySelector('{HINT}')).visibility === 'visible'"
)
# The bulk bar's shown controls, each with its box's vertical centre.
BAR = """() => [...document.querySelectorAll(
  '.bulk-bar > button:not([hidden]), .bulk-bar > .bulk-count')].map((node) => {
  const box = node.getBoundingClientRect();
  return Math.round(box.top + box.height / 2);
})"""

# How many of the first row's action icons sit on each of its lines
# (Pledges holds its four decision toggles two to a line, #2131).
PAIRS = """() => {
  const lines = new Map();
  const icons = '#table tbody tr:first-child .icon-button';
  for (const node of document.querySelectorAll(icons)) {
    const top = Math.round(node.getBoundingClientRect().top);
    lines.set(top, (lines.get(top) || 0) + 1);
  }
  return [...lines.values()];
}"""


def first_row_bottom(page):
    """The first table row's bottom edge, in window pixels, unscrolled."""
    return page.locator("#table tbody tr").first.evaluate(
        "node => node.getBoundingClientRect().bottom + scrollY"
    )


def hold_filters(page, held):
    """Make the Stage filter invalid (``held``) or valid again, as a field
    the browser refuses would, and let the Apply gate answer."""
    page.locator("#worklist-stage").evaluate(
        """(field, held) => {
          field.setCustomValidity(held ? 'Not a stage.' : '');
          field.dispatchEvent(new Event('input', {bubbles: true}));
          field.dispatchEvent(new Event('change', {bubbles: true}));
        }""",
        held,
    )


def test_the_first_row_ends_inside_a_laptop_window(page, component_origin):
    """At 1366×768 each worklist's whole first row is inside the window
    before any scroll, and its bulk bar keeps its buttons, Select all and
    the selected count on one line; Pledges' four decision toggles sit two
    to a line, which leaves its Family's answer the room it needs."""
    page.set_viewport_size({"width": 1366, "height": 768})
    for path in PAGES:
        page.goto(component_origin + path)
        page.evaluate("document.fonts.ready")
        assert first_row_bottom(page) <= 768, (path, first_row_bottom(page))
        centres = page.evaluate(BAR)
        assert len(centres) >= 3, path
        assert max(centres) - min(centres) <= 2, (path, centres)
        assert no_sideways_scroll(page), path
        if path == PLEDGES:
            assert page.evaluate(PAIRS) == [2, 2]


@pytest.mark.parametrize("width", [1366, 320])
def test_the_pledges_link_sits_on_the_heading_row(page, component_origin, width):
    """Pledges' Financial stewardship link shares the heading's row with
    About this page (on a phone, About's line), and opening About shows its
    help below that row without moving the link."""
    page.set_viewport_size({"width": width, "height": 768})
    page.goto(component_origin + PLEDGES)
    link = page.get_by_role("link", name="Financial stewardship", exact=True)
    about = page.locator("details.about-page > summary")
    closed = link.bounding_box()
    toggle = about.bounding_box()
    # Beside About, on its line.
    assert closed["x"] >= toggle["x"] + toggle["width"]
    assert closed["y"] < toggle["y"] + toggle["height"]
    assert closed["y"] + closed["height"] > toggle["y"]
    about.click()
    help_box = page.locator(".about-page-body").bounding_box()
    assert link.bounding_box() == closed
    assert help_box["y"] >= toggle["y"] + toggle["height"] - 1
    assert no_sideways_scroll(page)


def test_narrow_windows_never_scroll_sideways(page, component_origin):
    """At 320 px every worklist page fits the window's width."""
    page.set_viewport_size({"width": 320, "height": 768})
    for path in PAGES:
        page.goto(component_origin + path)
        assert no_sideways_scroll(page), path


def assert_held(page):
    """Apply filters is unavailable and names the hint, which says why."""
    apply = page.get_by_role("button", name="Apply filters")
    assert apply.is_disabled()
    assert (
        page.locator(HINT).get_attribute("id")
        in apply.get_attribute("aria-describedby").split()
    )
    has_text(page.locator(HINT), HELD)


@pytest.mark.parametrize("width", [1366, 320])
def test_the_filter_hint_shows_on_the_notice_line(page, component_origin, width):
    """With no notice, the filter form's reserved hint is drawn on the
    notice's reserved line, below the filters, while the filters cannot be
    applied, and empties when they can; the table never moves."""
    page.set_viewport_size({"width": width, "height": 768})
    errors = watch(page)
    page.goto(component_origin + PATH)
    before = page.evaluate(BOXES)
    assert before["hint"]["top"] == before["notice"]["top"]
    assert before["form"]["top"] + before["form"]["height"] <= before["hint"]["top"]
    assert before["hint"]["top"] + before["hint"]["height"] <= before["table"]["top"]
    hold_filters(page, True)
    assert_held(page)
    assert page.evaluate(HINT_SHOWN)
    assert page.evaluate(BOXES) == before
    hold_filters(page, False)
    eventually(page, CLEARED)
    assert page.evaluate(BOXES) == before
    assert_clean(page, errors)


@pytest.mark.parametrize("width", [1366, 320])
def test_a_notice_wins_the_line_while_the_filters_are_held(
    page, component_origin, width
):
    """A row decided while the filters are held shows its notice on the
    shared line in full, and the hint is not drawn over it; Apply filters
    stays unavailable and still names the hint. A notice already shown
    stays shown when the filters become held. Nothing moves."""
    page.set_viewport_size({"width": width, "height": 768})
    errors = watch(page)
    page.goto(component_origin + PATH)
    before = page.evaluate(BOXES)
    hold_filters(page, True)
    assert_held(page)
    posts_to(page, component_origin, ACCEPTED)
    icon(page, 0, "Accept").click()
    dialog = page.locator("#worklist-decide-dialog")
    dialog.get_by_role("button", name="Record").click()
    has_text(page.locator(NOTICE), ACCEPTED_NOTICE)
    assert page.evaluate(NOTICE_SHOWN)
    assert not page.evaluate(HINT_SHOWN)
    assert_held(page)
    assert page.evaluate(BOXES) == before
    # A notice drawn before the filters are held also stays readable.
    page.goto(component_origin + ACCEPTED)
    has_text(page.locator(NOTICE), ACCEPTED_NOTICE)
    hold_filters(page, True)
    assert_held(page)
    assert page.evaluate(NOTICE_SHOWN)
    assert not page.evaluate(HINT_SHOWN)
    assert page.evaluate(BOXES) == before
    assert_clean(page, errors)


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_shared_line_passes_axe_in_both_schemes(
    page, component_origin, axe_source, scheme
):
    """With the filters held, the page has no WCAG violation (contrast
    included) in the light and the dark scheme, at a laptop and a phone
    width, whether the line shows the hint or a decision's notice."""
    page.emulate_media(color_scheme=scheme)
    for width in (1366, 320):
        page.set_viewport_size({"width": width, "height": 768})
        for path, shown in ((PATH, HINT_SHOWN), (ACCEPTED, NOTICE_SHOWN)):
            page.goto(component_origin + path)
            assert page.evaluate("document.documentElement.dataset.theme") == scheme
            hold_filters(page, True)
            assert_held(page)
            assert page.evaluate(shown), (width, path)
            page.evaluate(axe_source)
            assert page.evaluate(AXE) == [], (width, path)
