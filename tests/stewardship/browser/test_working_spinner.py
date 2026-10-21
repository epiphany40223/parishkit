"""The shared working spinner in In progress stage boxes (#1847).

UX conventions "Working spinner" and "Stage boxes": an In progress box,
and setup's active downloaded collection, show the one shared spinner in
their mark's slot, only while In progress. It is hidden from assistive
technology, turns only without reduced motion, stays readable in Dark
mode, and its arrival and departure move nothing around it. The page is
setup's Loading parish data, which the live status poll moves on in place
(the same stage-boxes include the Campaign setup and Go live pages draw).
"""

import pytest

from .conftest import NOW, SETUP_LOAD_STATUS, load_collections
from .setup_live_components import load_status
from .test_go_live_prepare import STATES as PREPARE_STATES
from .test_production_activation import STATES as ACTIVATION_STATES
from .test_setup_live_status import answer, observation
from .waits import has_attribute

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

PHASES = "[data-load-phase]"
# Where each box's words are drawn: the label's and the status's top-left
# corners inside their box (their widths follow the words), each box's
# place in its list and its size, which every state keeps. Relative to the
# list, because the summary line above the list changes its words too.
WORDS = """boxes => boxes.map(box => {
  const r = box.getBoundingClientRect();
  const list = box.parentElement.getBoundingClientRect();
  const at = (node, from) => { const n = node.getBoundingClientRect();
    return [Math.round(n.left - from.left), Math.round(n.top - from.top)]; };
  return [at(box.querySelector('.stage-label'), r),
          at(box.querySelector('.stage-status'), r),
          at(box, list), [Math.round(r.width), Math.round(r.height)]];
})"""
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
  // Level with the label's first line: their middles within 2px.
  const line = label.getClientRects()[0];
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


def spinner_keys(page):
    """The keys of the stage boxes that draw a spinner."""
    return page.locator(PHASES).evaluate_all(
        "boxes => boxes.filter(b => b.querySelector('.spinner'))"
        ".map(b => b.dataset.loadPhase)"
    )


@pytest.mark.parametrize(
    ("width", "scheme"), [(320, "dark"), (1280, "light"), (1280, "dark")]
)
def test_in_progress_box_spinner_comes_and_goes_in_place(
    page, component_origin, width, scheme
):
    """The spinner shows only in the In progress box, in its mark's slot,
    and moving on (Download done, then everything done) moves no words."""
    page.set_viewport_size({"width": width, "height": 900})
    page.emulate_media(color_scheme=scheme, reduced_motion="no-preference")
    page.clock.install(time=NOW)
    page.route(
        "**/admin/setup/source/*?format=json",
        lambda route: route.fulfill(json=observation()),
    )
    saving = observation(phase="staging", current=1234, total=5000)
    saving["collections"] = load_collections(7)
    done = observation(
        phase="validating",
        task_state="succeeded",
        setup_state="collecting",
        active=False,
        current=9000,
        total=9000,
        collections=load_collections(7),
    )
    answer(
        page, SETUP_LOAD_STATUS, [load_status(saving, None), load_status(done, None)]
    )
    page.goto(component_origin + "/setup-source-progress")
    boxes = page.locator(PHASES)
    assert spinner_keys(page) == ["fetching"]
    spinner = boxes.locator(".spinner")
    assert spinner.get_attribute("aria-hidden") == "true"
    assert spinner.evaluate(SLOT)
    # The words carry the state; the spinner adds nothing to them.
    assert "In progress" in boxes.first.inner_text()
    assert boxes.first.evaluate("node => node.textContent").startswith("Download")
    ink, ring = spinner.evaluate(INK)
    assert ring == ink
    assert spinner.evaluate("node => getComputedStyle(node).animationName") == (
        "busy-spin"
    )
    # The active downloaded collection shows it too, and only that one.
    collections = page.locator("[data-load-collections] .spinner")
    assert collections.count() == 1
    assert collections.evaluate("node => node.parentElement.className") == (
        "setup-load-active"
    )
    assert collections.evaluate(SLOT)
    assert collections.evaluate(
        "node => getComputedStyle(node).borderTopColor"
        " === getComputedStyle(node.parentElement).color"
    )
    words = boxes.evaluate_all(WORDS)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    # Download done, Save in progress: the spinner moves to the next box.
    page.clock.fast_forward(2000)
    has_attribute(
        page.locator('[data-load-phase="staging"]'), "data-stage-state", "active"
    )
    assert spinner_keys(page) == ["staging"]
    assert page.locator("[data-load-collections] .spinner").count() == 0
    assert boxes.evaluate_all(WORDS) == words
    # Everything done: no spinner anywhere, and still nothing moved.
    page.clock.fast_forward(10000)
    has_attribute(boxes.last, "data-stage-state", "done")
    assert spinner_keys(page) == []
    assert boxes.evaluate_all(WORDS) == words
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def test_reduced_motion_shows_a_still_spinner(page, component_origin):
    """With reduced motion the In progress box's spinner is a still ring in
    the same place, not removed, so the box still reads as working."""
    page.emulate_media(reduced_motion="no-preference")
    page.goto(component_origin + "/setup-source-progress")
    spinner = page.locator(PHASES).locator(".spinner")
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
