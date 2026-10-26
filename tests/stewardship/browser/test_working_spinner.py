"""The shared working spinner in In progress stage boxes (#1847).

UX conventions "Working spinner" and "Stage boxes": an In progress box
shows the one shared spinner in its mark's slot, only while In progress. It
is hidden from assistive technology, turns only without reduced motion and
stays readable in Dark mode. The pages are the Go live page's Prepare and
Activation steps (the stage-boxes include Finishing setup draws too).
"""

import pytest

from .go_live_components import PREPARE
from .test_go_live_prepare import STATES as PREPARE_STATES
from .test_production_activation import STATES as ACTIVATION_STATES

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

# The spinner's place inside its box: it must sit in the mark's slot (the
# box's left padding, 2rem) before the label, inside the box. Layout offsets
# (the box is the spinner's offset parent) ignore the ring's rotation, which
# would otherwise grow its bounding rectangle as it turns.
SLOT = """spinner => {
  const box = spinner.parentElement;
  // A stage box's label is inside its words' body (#1903); a collection
  // row's label follows the spinner directly.
  const next = spinner.nextElementSibling;
  const label = next.querySelector('.stage-label') || next;
  const pad = parseFloat(getComputedStyle(box).paddingLeft);
  const right = spinner.offsetLeft + spinner.offsetWidth;
  // Level with the label's first line: their middles within 2px. A label
  // drawn as a block has one client rect however many lines it wraps to,
  // so the first line is measured from the first character's own box.
  const walker = document.createTreeWalker(label, NodeFilter.SHOW_TEXT, {
    acceptNode: node => node.textContent.trim()
      ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_SKIP,
  });
  const text = walker.nextNode();
  const range = document.createRange();
  const first = text.textContent.length - text.textContent.trimStart().length;
  range.setStart(text, first);
  range.setEnd(text, first + 1);
  const line = range.getClientRects()[0];
  const top = box.getBoundingClientRect().top + box.clientTop;
  const middle = top + spinner.offsetTop + spinner.offsetHeight / 2;
  return spinner.offsetParent === box && spinner.offsetWidth > 0
    && spinner.offsetLeft >= 0 && right <= pad && right <= label.offsetLeft
    && spinner.offsetTop >= 0
    && spinner.offsetTop + spinner.offsetHeight <= box.clientHeight
    && Math.abs(middle - (line.top + line.height / 2)) <= 2;
}"""
OFFSETS = """node => [node.offsetLeft, node.offsetTop, node.offsetWidth,
  node.offsetHeight]"""
# The design token the spinner's ring must take, resolved by the browser.
INK = """spinner => {
  const probe = document.createElement('span');
  probe.style.color = 'var(--warning-ink)';
  spinner.parentElement.append(probe);
  const ink = getComputedStyle(probe).color;
  probe.remove();
  return [ink, getComputedStyle(spinner).borderTopColor];
}"""


def test_reduced_motion_shows_a_still_spinner(page, component_origin):
    """With reduced motion the In progress box's spinner is a still ring in
    the same place, not removed, so the box still reads as working."""
    page.emulate_media(reduced_motion="no-preference")
    page.goto(component_origin + PREPARE)
    spinner = page.locator("[data-go-live-stage] .spinner")
    turning = spinner.evaluate(OFFSETS)
    page.emulate_media(reduced_motion="reduce")
    assert spinner.evaluate("node => getComputedStyle(node).animationName") == "none"
    assert spinner.is_visible()
    assert spinner.evaluate(OFFSETS) == turning
    assert spinner.evaluate(SLOT)


@pytest.mark.parametrize(("width", "scheme"), [(320, "dark"), (1280, "light")])
def test_go_live_in_progress_boxes_show_the_shared_spinner(
    page, component_origin, width, scheme
):
    """The Go live page's Prepare and Activation steps draw the one shared
    spinner in every In progress box and nowhere else, in the mark's slot."""
    page.set_viewport_size({"width": width, "height": 900})
    page.emulate_media(color_scheme=scheme, reduced_motion="no-preference")
    pages = [(row[0], "[data-go-live-stage]") for row in PREPARE_STATES]
    pages += [
        (row[0], "[data-production-stage]") for row in ACTIVATION_STATES if row[5]
    ]
    seen = 0
    for path, boxes in pages:
        page.goto(component_origin + path)
        states = page.locator(boxes).evaluate_all(
            "boxes => boxes.map(b => [b.dataset.stageState,"
            " b.querySelectorAll('.spinner').length])"
        )
        # A spinner in an active box, none in any other.
        assert [n for _, n in states] == [
            1 if state == "active" else 0 for state, _ in states
        ], path
        spinners = page.locator(f"{boxes} .spinner")
        for index in range(spinners.count()):
            seen += 1
            spinner = spinners.nth(index)
            assert spinner.get_attribute("aria-hidden") == "true", path
            assert spinner.evaluate(SLOT), path
            ink, ring = spinner.evaluate(INK)
            assert ring == ink, path
        assert page.evaluate(
            "document.documentElement.scrollWidth <= window.innerWidth"
        ), path
    assert seen >= 4
