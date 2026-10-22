"""The Campaign setup stepper's layout, action row and accessibility (#142, #1404)."""

from uuid import UUID

import pytest
from django.urls import reverse

from .review_helpers import answer_reviews
from .stepper_components import (
    AWAITED_STATUS,
    CONTENT,
    EDITOR,
    LOAD,
    LOAD_AWAITED,
    LOAD_AWAITED_STATUS,
    LOAD_EMPTY,
    LOAD_FAILED,
    LOAD_OFFER_STATUS,
    LOAD_RUN,
    LOAD_STATUS,
    LOAD_STATUS_DONE,
    LOAD_STATUS_EMPTY,
    LOAD_STATUS_QUEUED,
    LOAD_STATUS_RUNNING,
    LOAD_STATUS_STARTED,
    LOADED,
    LOADING,
    PATH,
    REVIEW,
)
from .test_components import axe_violations, shown_step_list
from .test_in_place import MARK, MARKED
from .waits import (
    available,
    eventually,
    greyed,
    has_attribute,
    has_text,
    hidden,
    visible,
)

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)


@pytest.mark.parametrize("width", [320, 1280])
def test_campaign_stepper_says_whats_next_and_ends_with_its_action_row(
    page, component_origin, axe_source, width
):
    """The summary names the next step; Back and Continue are at the bottom.

    The full list is open on a wide screen and a disclosure on a phone.
    """
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + PATH)
    stepper = page.locator("#campaign-stepper")
    text = stepper.inner_text()
    assert "Step 3 of 8: Share options" in text
    assert "1 of 8 steps completed" in text
    assert stepper.locator("[data-campaign-step-next]").inner_text() == (
        "Next: Pages and emails"
    )
    leave = stepper.locator("[data-campaign-leave]")
    assert leave.inner_text() == "Leave campaign setup"
    assert leave.get_attribute("href") == reverse("admin:index")
    # The stepper itself never holds a form or a button that could save.
    assert stepper.locator("form, button").count() == 0
    steps = shown_step_list(page, stepper, width)
    assert "Share options" in steps.locator('[aria-current="step"]').inner_text()
    assert "Completed" in steps.locator(".setup-step-done").inner_text()
    # The action row at the bottom: Back, and Continue in a form of its own.
    row = page.locator("[data-campaign-step-actions]")
    back = row.locator("[data-campaign-step-back]")
    assert back.get_attribute("href") == reverse("admin:source_refresh")
    proceed = row.get_by_role("button", name="Continue to Pages and emails")
    visible(proceed)
    assert proceed.is_enabled()
    assert row.locator("form").get_attribute("action") == reverse(
        "admin:campaign_step_continue"
    )
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert axe_violations(page, axe_source) == []
    assert failures == []


def test_continue_waits_while_the_form_holds_a_change(page, component_origin):
    """A change on the page holds Continue, with its reserved hint; nothing moves."""
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.goto(component_origin + PATH)
    proceed = page.get_by_role("button", name="Continue to Pages and emails")
    hint = page.locator("#campaign-continue-hint")
    assert proceed.is_enabled() and not hint.is_visible()
    row = page.locator("[data-campaign-step-actions]")

    def placed():
        """Continue's place and size within its row, with the hint's height.

        The page above may grow while it holds a change (Share options
        previews its options), so the check is relative to the row.
        """
        box, frame = proceed.bounding_box(), row.bounding_box()
        return (
            box["x"] - frame["x"],
            box["y"] - frame["y"],
            box["width"],
            box["height"],
            hint.bounding_box()["height"],
        )

    def unmoved():
        """Whether Continue and its hint sit as before, within a pixel.

        Firefox rounds a box to whole pixels differently as the page above
        it changes (a 1px step seen in CI), so an exact compare flakes; a
        moved control moves by at least a line.
        """
        pairs = zip(placed(), before, strict=True)
        return all(abs(now - then) < 1.5 for now, then in pairs)

    before = placed()
    field = page.locator("#id_options-0-label")
    saved = field.input_value()
    field.fill("Renamed option")
    greyed(proceed)
    visible(hint)
    assert proceed.get_attribute("aria-describedby") == "campaign-continue-hint"
    assert unmoved(), (placed(), before)
    # Undoing the change gives Continue back.
    field.fill(saved)
    available(proceed)
    assert not hint.is_visible() and proceed.get_attribute("aria-describedby") is None
    assert unmoved(), (placed(), before)
    assert failures == []


def test_apply_in_the_review_dialog_marks_the_step_in_place(page, component_origin):
    """Once the dialog's Apply is applied, the page's redraw marks the step.

    The dialog redraws every region from the page's own address, the
    stepper among them: Share options reads completed, with no reload, and
    Continue is available again over the saved values.
    """
    answer_reviews(page, component_origin, REVIEW, address=PATH)
    page.goto(component_origin + PATH)
    page.evaluate(MARK)
    stepper = page.locator("#campaign-stepper")
    page.locator("#id_options-0-label").fill("Renamed option")
    page.get_by_role("button", name="Save", exact=True).click()
    dialog = page.get_by_role("dialog", name="Save the share options?")
    dialog.get_by_role("button", name="Apply").click()
    hidden(dialog)
    has_text(stepper.locator(".setup-stepper-count"), "2 of 8 steps completed")
    assert stepper.locator("[data-campaign-step-next]").inner_text() == (
        "Next: Pages and emails"
    )
    available(page.get_by_role("button", name="Continue to Pages and emails"))
    assert page.evaluate(MARKED)


def test_continue_opens_the_next_step_and_a_sub_page_returns_to_it(
    page, component_origin
):
    """Continue on unchanged Share options opens Pages and emails (#1404).

    Its editor, a sub-page, draws the stepper with Pages and emails current
    and Return to Pages and emails, linking that page, where Back sits.
    """
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.goto(component_origin + PATH)
    page.get_by_role("button", name="Continue to Pages and emails").click()
    page.wait_for_url(component_origin + CONTENT)
    stepper = page.locator("#campaign-stepper")
    assert "Step 4 of 8: Pages and emails" in stepper.inner_text()
    assert "2 of 8 steps completed" in stepper.inner_text()
    page.goto(component_origin + EDITOR)
    assert "Step 4 of 8: Pages and emails" in stepper.inner_text()
    row = page.locator("[data-campaign-step-actions]")
    assert row.get_by_role("button").count() == 0
    back = row.get_by_role("link", name="Return to Pages and emails")
    assert back.get_attribute("href") == reverse("admin:content_catalog")
    assert failures == []


# What the step's announcer says once a load is requested: the run is
# queued, so Start the load is the box in progress (#1916).
SPOKEN_QUEUED = "Loading the campaign's ParishSoft data. Start the load: In progress."

# The regular Refresh from ParishSoft page's queue wording (D2.1), which
# the step never shows.
QUEUE = "No second refresh starts beside it"


def contains(locator, text):
    """Assert that ``locator`` comes to contain ``text``, waiting."""
    from playwright.sync_api import expect

    expect(locator).to_contain_text(text)


def stage_states(page):
    """Each of the step's stage boxes as (stage key, state), in order."""
    boxes = page.locator("#refresh-run [data-load-stage]")
    return [
        (
            boxes.nth(i).get_attribute("data-load-stage"),
            boxes.nth(i).get_attribute("data-stage-state"),
        )
        for i in range(boxes.count())
    ]


def assert_no_refresh_wording(page):
    """The step shows no Refresh now and none of the refresh queue wording."""
    assert page.get_by_role("button", name="Refresh now").count() == 0
    assert page.get_by_text(QUEUE, exact=False).count() == 0


@pytest.mark.parametrize("width", [320, 660, 1024, 1280])
def test_load_step_loads_then_continues_in_place(
    page, component_origin, axe_source, width
):
    """Load ParishSoft data shows one state at a time, in place (#1404 PR 5).

    Not loaded: every stage box waiting and the load button, Back only.
    Loading (its in-place answer): the boxes follow the run's polled status,
    with no button and no Continue. When the run ends, the page is re-read
    in place: every box done, the Family count, and Continue, with no
    reload, no Refresh now, and the summary line and boxes in the same place.
    """
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(answer_load(page, component_origin, LOAD, LOADED))
    page.evaluate(MARK)
    stepper = page.locator("#campaign-stepper")
    assert "Step 2 of 8: Load ParishSoft data" in stepper.inner_text()
    assert page.locator("[data-campaign-load]").count() == 0
    region = page.locator("#refresh-run")
    summary = region.locator("[data-campaign-load-summary]")
    assert (
        summary.inner_text()
        == "This campaign's ParishSoft data hasn't been loaded yet."
    )
    assert [state for _key, state in stage_states(page)] == ["waiting"] * 5
    boxes = region.get_by_role("list", name="Parts of the load")
    # Each box's state is in its text, so in its name as a list item.
    first = boxes.get_by_role("listitem").first
    assert first.evaluate("node => node.textContent") == ("Start the load: Not started")
    stage_boxes = region.locator("[data-load-stage]")
    sizes = boxes_sizes(stage_boxes)
    waiting = background(first)
    row = page.locator("[data-campaign-step-actions]")
    back = row.locator("[data-campaign-step-back]")
    assert back.get_attribute("href") == reverse("admin:campaign_settings")
    assert row.get_by_role("link", name="Continue to Share options").count() == 0
    assert_no_refresh_wording(page)
    start = page.get_by_role("button", name="Load the campaign's ParishSoft data")
    visible(start)
    assert "secondary" not in (start.get_attribute("class") or "")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert axe_violations(page, axe_source) == []
    polls = answer_polls(page, component_origin)
    start.scroll_into_view_if_needed()
    above = summary.bounding_box()
    # The action row below the button: its Back must not move up when the
    # pressed button goes (#1517 review, L1).
    below = back.bounding_box()
    page.evaluate(SNAPSHOT_SPOKEN)
    start.click()
    has_text(summary, "Loading the campaign's ParishSoft data…")
    assert region.get_by_role("button").count() == 0
    assert row.get_by_role("link", name="Continue to Share options").count() == 0
    assert_no_refresh_wording(page)
    assert_same_box(above, summary.bounding_box())
    assert_same_box(below, back.bounding_box())
    assert_spoken_once(page, SPOKEN_QUEUED)
    first_box = region.locator("[data-load-stage]").first.bounding_box()
    # The poll moves the boxes on in place: Start the load and Download
    # done, Save in progress.
    has_attribute(
        region.locator('[data-load-stage="staging"]'), "data-stage-state", "active"
    )
    assert stage_states(page)[:3] == [
        ("start", "done"),
        ("fetching", "done"),
        ("staging", "active"),
    ]
    # Each state has its own colour, Not started the page's own, In progress
    # yellow, Done light green, in light and dark, and no box changes size
    # as its state changes (UX conventions, "Stage boxes").
    assert_state_colours(page, stage_boxes, waiting)
    assert boxes_sizes(stage_boxes) == sizes
    has_text(
        region.locator("[data-campaign-load-count]"), "1,000 of 4,000 records checked"
    )
    assert axe_violations(page, axe_source) == []
    polls.finish()
    # The run ends: every box done, then the page re-read in place brings
    # the Family count and Continue.
    proceed = row.get_by_role("link", name="Continue to Share options")
    visible(proceed)
    assert proceed.get_attribute("href") == reverse("admin:share_settings")
    has_text(summary, "The campaign's ParishSoft data is loaded.")
    count = region.locator("[data-campaign-load-count]")
    contains(count, "1,234 Families, as of")
    assert "UTC" not in count.inner_text()
    assert [state for _key, state in stage_states(page)] == ["done"] * 5
    assert region.get_by_role("button").count() == 0
    assert_no_refresh_wording(page)
    assert "Step 2 of 8: Load ParishSoft data" in stepper.inner_text()
    assert_same_box(above, summary.bounding_box())
    assert_same_box(first_box, region.locator("[data-load-stage]").first.bounding_box())
    assert_same_box(below, back.bounding_box())
    assert boxes_sizes(stage_boxes) == sizes
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert page.evaluate(MARKED) == "kept"
    assert len(polls) >= 2
    assert failures == []


class Polls(list):
    """The step's status polls answered; ``finish`` ends the run."""

    done = False

    def finish(self):
        """From the next poll on, answer that the run has finished."""
        self.done = True


def answer_polls(page, origin, done=LOAD_STATUS_DONE, running=LOAD_STATUS_RUNNING):
    """Answer the step's status polls: ``running`` until ``finish``, then ``done``.

    Returns the ``Polls`` answered.
    """
    polls = Polls()

    def answer(route):
        """Serve the running status until the test finishes the run."""
        polls.append(route.request.url)
        shown = done if polls.done else running
        route.fulfill(response=route.fetch(url=origin + shown, method="GET"))

    page.route(lambda url: url.split("?")[0] == origin + LOAD_STATUS, answer)
    return polls


def background(locator):
    """A box's computed background colour."""
    return locator.evaluate("node => getComputedStyle(node).backgroundColor")


def boxes_sizes(boxes):
    """Each stage box's width and height, rounded to the pixel."""
    return [
        (round(box["width"]), round(box["height"]))
        for box in (boxes.nth(i).bounding_box() for i in range(boxes.count()))
    ]


def assert_state_colours(page, boxes, waiting):
    """Assert Done, In progress and Not started boxes differ in colour.

    ``boxes`` show Start the load and Download done, Save in progress and the
    rest not started;
    ``waiting`` is a Not started box's colour in light mode. Dark mode's
    colours differ from each other too, and from light mode's.
    """
    done, active, rest = boxes.nth(1), boxes.nth(2), boxes.nth(3)
    light = (background(done), background(active), background(rest))
    assert light[2] == waiting
    assert len(set(light)) == 3, light
    scheme(page, "dark")
    try:
        dark = (background(done), background(active), background(rest))
        assert len(set(dark)) == 3 and dark[:2] != light[:2], (light, dark)
    finally:
        scheme(page, "light")


def scheme(page, name):
    """Emulate the device colour scheme ``name`` and wait for the page to take it.

    The Admin dark tokens hang off ``html[data-theme]``, which admin-gate-v1.js
    sets from a ``matchMedia`` change listener that runs after
    ``emulate_media`` returns. Reading colours before it runs sees the old
    scheme's, as WebKit often did (#1638).
    """
    page.emulate_media(color_scheme=name)
    eventually(page, "document.documentElement.dataset.theme", name)


# Every live region's text, keyed by the node, kept on the page for a
# later comparison (``assert_spoken_once``).
SNAPSHOT_SPOKEN = """() => {
  const live = document.querySelectorAll('[role="status"], [aria-live]');
  window.spokenBefore = new Map([...live]
    .map((node) => [node, node.textContent.trim()]));
}"""
CHANGED_SPOKEN = """() => [...document.querySelectorAll('[role="status"], [aria-live]')]
  .filter((node) => node.textContent.trim()
    && node.textContent.trim() !== window.spokenBefore.get(node))
  .map((node) => node.textContent.trim())"""


def assert_spoken_once(page, sentence):
    """Assert the press was announced once: ``sentence``, in one live region.

    Compares with the live regions' text before the press (``SNAPSHOT_SPOKEN``
    run then). The button's form is data-in-place-spoken, so ui-v1.js's own
    announcer stays silent and only the region's live-status announcer
    speaks (#1517 review, L3). Waits past ui-v1.js's short announcement
    delay.
    """
    announcer = page.locator('[data-live-announcer="campaign-load"]')
    has_text(announcer, sentence)
    page.wait_for_timeout(300)
    spoken = page.evaluate(CHANGED_SPOKEN)
    assert len(spoken) == 1 and spoken[0].startswith(sentence), spoken


def assert_same_box(before, after):
    """Assert a control kept its place and size, to within 1 px (#736)."""
    for key in ("x", "y", "width", "height"):
        assert abs(before[key] - after[key]) <= 1, (key, before, after)


def answer_load(page, origin, first, reread=None):
    """Serve the step at the real refresh address; return that address.

    The page's load answers ``first``; the form's in-place POST answers
    ``LOADING``, as the server would after recording the load (redirecting
    back to this same address), and a later in-place re-read (once the run
    ends) answers ``reread``. The page and its answers must share an address
    for them to be swapped in place.
    """
    address = origin + reverse("admin:source_refresh")

    def answer(route):
        """Serve ``first`` for a load, ``LOADING`` for the POST, else ``reread``."""
        if route.request.method == "POST":
            shown = LOADING
        elif route.request.resource_type == "document" or reread is None:
            shown = first
        else:
            shown = reread
        route.fulfill(response=route.fetch(url=origin + shown, method="GET"))

    page.route(lambda url: url.split("#")[0] == address, answer)
    return address


@pytest.mark.parametrize("width", [320, 660, 1024, 1280])
def test_failed_load_says_so_and_try_again_loads_again(page, component_origin, width):
    """After a failed load the step says so, with Try again (#1404 PR 5).

    The stage the run stopped in is marked failed, in words; See what went
    wrong opens its Background task; Try again's in-place answer shows the
    load under way, with no button and no Continue, and no reload, and the
    action row below keeps its place.
    """
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(answer_load(page, component_origin, LOAD_FAILED))
    page.evaluate(MARK)
    region = page.locator("#refresh-run")
    summary = region.locator("[data-campaign-load-summary]")
    assert "The campaign's ParishSoft data did not load." in summary.inner_text()
    link = summary.get_by_role("link", name="See what went wrong")
    assert link.get_attribute("href") == reverse(
        "admin:background_task_page", args=[UUID(int=1404)]
    )
    assert stage_states(page)[:3] == [
        ("start", "done"),
        ("fetching", "done"),
        ("staging", "failed"),
    ]
    assert "Failed" in region.locator('[data-load-stage="staging"]').inner_text()
    row = page.locator("[data-campaign-step-actions]")
    assert row.get_by_role("link", name="Continue to Share options").count() == 0
    assert_no_refresh_wording(page)
    again = region.get_by_role("button", name="Try again")
    again.scroll_into_view_if_needed()
    above = summary.bounding_box()
    back = row.locator("[data-campaign-step-back]")
    below = back.bounding_box()
    page.evaluate(SNAPSHOT_SPOKEN)
    again.click()
    has_text(summary, "Loading the campaign's ParishSoft data…")
    assert region.get_by_role("button").count() == 0
    assert row.get_by_role("link", name="Continue to Share options").count() == 0
    # The in-place answer is the run just queued: Start the load is in
    # progress at once (#1916).
    assert [state for _key, state in stage_states(page)] == ["active"] + ["waiting"] * 4
    assert_same_box(above, summary.bounding_box())
    assert_same_box(below, back.bounding_box())
    assert_spoken_once(page, SPOKEN_QUEUED)
    assert page.evaluate(MARKED) == "kept"
    assert failures == []


@pytest.mark.parametrize("width", [320, 400, 641, 660, 700, 760, 1024, 1280, 1600])
def test_load_with_no_families_has_its_own_state(page, component_origin, width):
    """A full load that matched no Families says so, in place (#1587).

    Its wording, links to the run and Refresh from ParishSoft, every box done, no
    button of any kind (not the plain Load again, not Continue); the summary
    line, boxes and the action row below keep their places from loading to
    this state, in the poll's answer and after the page's in-place re-read,
    at phone, narrow desktop (just above the 40rem breakpoint) and wide widths.
    """
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(answer_load(page, component_origin, LOAD, LOAD_EMPTY))
    page.evaluate(MARK)
    region = page.locator("#refresh-run")
    summary = region.locator("[data-campaign-load-summary]")
    row = page.locator("[data-campaign-step-actions]")
    back = row.locator("[data-campaign-step-back]")
    polls = answer_polls(page, component_origin, LOAD_STATUS_EMPTY)
    start = region.get_by_role("button", name="Load the campaign's ParishSoft data")
    start.click()
    has_text(summary, "Loading the campaign's ParishSoft data…")
    first_box = region.locator("[data-load-stage]").first.bounding_box()
    above, below = summary.bounding_box(), back.bounding_box()
    polls.finish()
    contains(summary, "The campaign's ParishSoft data loaded, but no Families matched.")
    assert summary.get_by_role("link", name="See the load").get_attribute(
        "href"
    ) == reverse("admin:background_task_page", args=[LOAD_RUN])
    assert summary.get_by_role("link", name="Refresh from ParishSoft").get_attribute(
        "href"
    ) == reverse("admin:source_refresh")
    assert [state for _key, state in stage_states(page)] == ["done"] * 5
    assert region.get_by_role("button").count() == 0
    assert row.get_by_role("link", name="Continue to Share options").count() == 0
    assert_no_refresh_wording(page)
    assert_same_box(above, summary.bounding_box())
    assert_same_box(first_box, region.locator("[data-load-stage]").first.bounding_box())
    assert_same_box(below, back.bounding_box())
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert page.evaluate(MARKED) == "kept"
    assert failures == []


@pytest.mark.parametrize("width", [320, 660, 1280])
def test_a_queued_load_shows_start_the_load_at_once_and_nothing_moves(
    page, component_origin, width
):
    """While the requested load waits for a worker, the page says so (#1916).

    Right after the click, in the in-place answer and before any worker has
    the run, Start the load is In progress with the shared working spinner
    and the line after the boxes says the run waits for the background
    service; the polls keep saying so while it stays queued. When a worker
    starts the download, Start the load is Done, Download from ParishSoft In
    progress with the spinner, the line gives way to the records checked,
    and nothing moves: no box, the summary line, the line after the boxes
    nor the action row below.
    """
    from parishkit.stewardship.accounts.stages import QUEUED

    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(answer_load(page, component_origin, LOAD))
    region = page.locator("#refresh-run")
    summary = region.locator("[data-campaign-load-summary]")
    count = region.locator("[data-campaign-load-count]")
    start_box = region.locator('[data-load-stage="start"]')
    download = region.locator('[data-load-stage="fetching"]')
    back = page.locator("[data-campaign-step-actions] [data-campaign-step-back]")
    assert start_box.locator(".spinner").count() == 0
    polls = answer_polls(
        page, component_origin, LOAD_STATUS_STARTED, running=LOAD_STATUS_QUEUED
    )
    region.get_by_role("button", name="Load the campaign's ParishSoft data").click()
    has_text(summary, "Loading the campaign's ParishSoft data…")
    has_attribute(start_box, "data-stage-state", "active")
    visible(start_box.locator(".spinner"))
    assert start_box.locator(".stage-status").inner_text() == "In progress"
    assert count.inner_text() == str(QUEUED)
    assert download.get_attribute("data-stage-state") == "waiting"
    assert download.locator(".spinner").count() == 0
    stage_boxes = region.locator("[data-load-stage]")
    boxes = [stage_boxes.nth(i).bounding_box() for i in range(5)]
    places = [summary.bounding_box(), count.bounding_box(), back.bounding_box()]
    # A poll answered while the run is still queued keeps saying so.
    for _ in range(100):
        if polls:
            break
        page.wait_for_timeout(100)
    assert len(polls) == 1
    page.wait_for_timeout(100)
    assert start_box.get_attribute("data-stage-state") == "active"
    assert count.inner_text() == str(QUEUED)
    polls.finish()
    has_attribute(download, "data-stage-state", "active")
    assert stage_states(page)[:2] == [("start", "done"), ("fetching", "active")]
    assert start_box.locator(".spinner").count() == 0
    visible(download.locator(".spinner"))
    has_text(count, "1,000 of 4,000 records checked")
    for before, i in zip(boxes, range(5), strict=True):
        assert_same_box(before, stage_boxes.nth(i).bounding_box())
    for before, now in zip(
        places,
        [summary.bounding_box(), count.bounding_box(), back.bounding_box()],
        strict=True,
    ):
        assert_same_box(before, now)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert failures == []


def answer_awaited(page, origin, *fixtures):
    """Answer the step's own passive status (#1951) with each of ``fixtures``
    in turn, the last from then on; return the reads answered."""
    reads = []

    def answer(route):
        """Serve the next fixture to the step's poll."""
        reads.append(route.request.url)
        shown = fixtures[min(len(reads), len(fixtures)) - 1]
        route.fulfill(response=route.fetch(url=origin + shown, method="GET"))

    page.route(lambda url: url.split("?")[0] == origin + AWAITED_STATUS, answer)
    return reads


def awaited_places(page):
    """The load region's lines, each stage box and the action row's Back."""
    region = page.locator("#refresh-run")
    parts = [
        region.locator("[data-campaign-load-summary]"),
        region.locator("[data-campaign-load-count]"),
        page.locator("[data-campaign-step-actions] [data-campaign-step-back]"),
    ]
    boxes = region.locator("[data-load-stage]")
    parts += [boxes.nth(i) for i in range(5)]
    return [part.bounding_box() for part in parts]


@pytest.mark.parametrize("width", [320, 660, 1280])
def test_a_new_campaign_opens_on_the_load_the_system_starts(
    page, component_origin, width
):
    """Just created, the step already shows its load, in place (#1951).

    Before the system's own load is requested the step is loading, with
    Start the load In progress and its spinner, the queued sentence and no
    button; its own passive status then finds the run, hands the region
    over to the run's status, and the run ends with the page re-read in
    place. Nothing moves and the page never reloads.
    """
    from parishkit.stewardship.accounts.stages import QUEUED

    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.set_viewport_size({"width": width, "height": 900})
    awaited = answer_awaited(
        page,
        component_origin,
        LOAD_AWAITED_STATUS,
        LOAD_STATUS_QUEUED,
        LOAD_STATUS_STARTED,
    )
    polls = answer_polls(page, component_origin)
    page.goto(answer_load(page, component_origin, LOAD_AWAITED, LOADED))
    page.evaluate(MARK)
    region = page.locator("#refresh-run")
    summary = region.locator("[data-campaign-load-summary]")
    start_box = region.locator('[data-load-stage="start"]')
    download = region.locator('[data-load-stage="fetching"]')
    row = page.locator("[data-campaign-step-actions]")
    assert summary.inner_text() == "Loading the campaign's ParishSoft data…"
    assert start_box.get_attribute("data-stage-state") == "active"
    visible(start_box.locator(".spinner"))
    assert region.locator("[data-campaign-load-count]").inner_text() == str(QUEUED)
    assert region.get_by_role("button").count() == 0
    assert row.get_by_role("link", name="Continue to Share options").count() == 0
    places = awaited_places(page)
    # The run is requested and a worker starts it: Start the load Done, the
    # download In progress, from the step's own status. That is its third
    # answer, after the poller's 2 s, 2 s and 3 s delays, so the wait is
    # longer than Playwright's 5 s default.
    from playwright.sync_api import expect

    expect(download).to_have_attribute("data-stage-state", "active", timeout=30_000)
    assert start_box.get_attribute("data-stage-state") == "done"
    assert len(awaited) >= 3
    for before, now in zip(places, awaited_places(page), strict=True):
        assert_same_box(before, now)
    # From then on the region polls the run's own status.
    for _ in range(150):
        if polls:
            break
        page.wait_for_timeout(100)
    assert polls, awaited
    polls.finish()
    # The poller has backed off to several seconds between reads by now.
    proceed = row.get_by_role("link", name="Continue to Share options")
    expect(proceed).to_be_visible(timeout=30_000)
    has_text(summary, "The campaign's ParishSoft data is loaded.")
    assert [state for _key, state in stage_states(page)] == ["done"] * 5
    for before, now in zip(places, awaited_places(page), strict=True):
        assert_same_box(before, now)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert page.evaluate(MARKED) == "kept"
    assert failures == []


@pytest.mark.parametrize("width", [320, 1280])
def test_no_system_load_in_time_brings_the_load_button_in_place(
    page, component_origin, width
):
    """When the wait for the system's load ends with none, the step's own
    status settles and the page re-reads itself in place to the offer, with
    its Load button, and the summary line and boxes stay put (#1951)."""
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.set_viewport_size({"width": width, "height": 900})
    answer_awaited(page, component_origin, LOAD_AWAITED_STATUS, LOAD_OFFER_STATUS)
    page.goto(answer_load(page, component_origin, LOAD_AWAITED, LOAD))
    page.evaluate(MARK)
    region = page.locator("#refresh-run")
    places = awaited_places(page)
    start = region.get_by_role("button", name="Load the campaign's ParishSoft data")
    visible(start)
    assert (
        region.locator("[data-campaign-load-summary]").inner_text()
        == "This campaign's ParishSoft data hasn't been loaded yet."
    )
    assert [state for _key, state in stage_states(page)] == ["waiting"] * 5
    # The button's slot is reserved in every state, so its coming moves
    # nothing: the lines, the boxes and the action row's Back stay put.
    for before, now in zip(places, awaited_places(page), strict=True):
        assert_same_box(before, now)
    assert page.evaluate(MARKED) == "kept"
    assert failures == []
