"""Review these one at a time on each review table (#2123 slice 4).

Review pipeline spec, "Wizard entry points": the button sits first in each
table's selection bar; with unfinished runs it asks first in a dialog
(Continue per run, Start a new run, Cancel) and moves nothing; Start a new
run posts the table's view to the wizard's start; with no runs it posts at
once; on an empty table it is unavailable with its reason.
"""

from urllib.parse import parse_qs

import pytest

from .review_wizard_entry_components import EMPTY, NO_RUNS, PATHS, RUN
from .test_family_response import expect
from .test_review_wizard import AXE
from .waits import focused

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)
LABEL = "Review these one at a time"
# Each selection bar button's place within the bar (its line and left
# edge), by its label: the same whether the table lists rows or not.
BAR = """() => {
  const bar = document.querySelector('.bulk-bar').getBoundingClientRect();
  return [...document.querySelectorAll('.bulk-bar > button:not([hidden])')].map(
    node => { const box = node.getBoundingClientRect();
      return [node.textContent.trim(), Math.round(box.x - bar.x),
        Math.round(box.y - bar.y)]; }); }"""
START = "**/admin/reports/review/"


def captured(page):
    """Answer the wizard's start with plain text; return the posts it got."""
    posts = []

    def handle(route):
        """Note a POST's fields, then answer it."""
        if route.request.method == "POST":
            posts.append(parse_qs(route.request.post_data or ""))
        route.fulfill(body="Started")

    page.route(START, handle)
    return posts


def box(locator):
    """A control's place on the page (not the window, which may scroll to
    it), rounded."""
    return tuple(
        locator.evaluate(
            """node => { const b = node.getBoundingClientRect();
              return [b.x + scrollX, b.y + scrollY, b.width, b.height]
                .map(Math.round); }"""
        )
    )


@pytest.mark.parametrize("width", [320, 1366])
@pytest.mark.parametrize("stream", sorted(PATHS))
def test_each_table_asks_first_then_starts_a_new_run(
    page, component_origin, axe_source, stream, width
):
    """The button opens its dialog without moving; Cancel and Escape close it
    with focus back on the button; Start a new run posts the table's view."""
    page.set_viewport_size({"width": width, "height": 800})
    posts = captured(page)
    page.goto(component_origin + PATHS[stream])
    button = page.get_by_role("button", name=LABEL)
    expect(button).to_be_visible()
    expect(button).to_be_enabled()
    # First in the selection bar.
    assert button.evaluate(
        "node => node.closest('.bulk-bar').querySelector('button') === node"
    )
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    before = box(button)
    button.click()
    dialog = page.get_by_role("dialog", name=LABEL)
    expect(dialog).to_be_visible()
    expect(
        dialog.get_by_text(
            "You have unfinished runs. Continue an existing run or start a new one?"
        )
    ).to_be_visible()
    cancel = dialog.get_by_role("button", name="Cancel")
    expect(cancel).to_be_focused()
    expect(
        dialog.get_by_role("link", name="Continue Census changes, Family 12 of 87")
    ).to_have_attribute("href", f"/admin/reports/review/12/?run={RUN}")
    expect(
        dialog.get_by_role("link", name="Continue Pledges, Family 3 of 9")
    ).to_be_visible()
    page.evaluate(axe_source)
    assert page.evaluate(AXE) == []
    # The dialog is inside the window at either width.
    found = dialog.bounding_box()
    assert found["x"] >= 0 and found["x"] + found["width"] <= width
    cancel.click()
    expect(dialog).to_be_hidden()
    focused(button)
    assert box(button) == before
    button.click()
    expect(dialog).to_be_visible()
    page.keyboard.press("Escape")
    expect(dialog).to_be_hidden()
    focused(button)
    assert posts == []
    button.click()
    dialog.get_by_role("button", name="Start a new run").click()
    page.wait_for_url("**/admin/reports/review/")
    assert len(posts) == 1
    sent = {name: values[0] for name, values in posts[0].items()}
    assert sent["stream"] == stream
    assert (sent["mode"], sent["table"], sent["new"]) == ("advanced", "1", "1")
    assert sent["sort"] in {"name", "oldest", "newest"} and "stage" in sent


def test_without_runs_the_button_starts_at_once(page, component_origin):
    """No unfinished run: one click posts the table's view, with no dialog."""
    page.set_viewport_size({"width": 1366, "height": 800})
    posts = captured(page)
    page.goto(component_origin + NO_RUNS)
    expect(page.locator("dialog[data-wizard-dialog]")).to_have_count(0)
    page.get_by_role("button", name=LABEL).click()
    page.wait_for_url("**/admin/reports/review/")
    assert len(posts) == 1
    sent = {name: values[0] for name, values in posts[0].items()}
    assert sent["stream"] == "census" and "new" not in sent


@pytest.mark.parametrize("width", [320, 1366])
def test_an_empty_table_shows_the_button_unavailable(page, component_origin, width):
    """Nothing listed: the button is unavailable, described by its visible
    reason on a line of its own, so the bar's other buttons keep their
    places; it posts nothing."""
    page.set_viewport_size({"width": width, "height": 800})
    posts = captured(page)
    page.goto(component_origin + PATHS["ministry"])
    listed = page.evaluate(BAR)
    page.goto(component_origin + EMPTY)
    # Select all is shown only while there are rows to select.
    empty = {label: place for label, *place in page.evaluate(BAR)}
    listed = {label: place for label, *place in listed if label != "Select all"}
    assert empty == listed and LABEL in empty
    button = page.get_by_role("button", name=LABEL)
    expect(button).to_be_disabled()
    reason = page.get_by_text("Nothing is listed to review one at a time.")
    expect(reason).to_be_visible()
    described = button.get_attribute("aria-describedby")
    assert reason.get_attribute("id") == described
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    # Disabled, so neither a click nor Enter can submit its form.
    assert button.evaluate("node => node.disabled && node.form !== null")
    button.click(force=True)
    page.wait_for_load_state()
    assert page.url == component_origin + EMPTY and posts == []
