"""The Campaign setup stepper's layout, action row and accessibility (#142, #1404)."""

from uuid import UUID

import pytest
from django.urls import reverse

from .artwork_components import STEP as ARTWORK_STEP
from .review_helpers import answer_reviews
from .stepper_components import (
    CONTENT,
    EDITOR,
    LOAD,
    LOAD_FAILED,
    LOADING,
    PATH,
    REVIEW,
)
from .test_artwork_dialog import png, slot
from .test_components import axe_violations, shown_step_list
from .test_in_place import MARK, MARKED
from .waits import available, greyed, has_text, hidden, visible

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


def test_continue_waits_while_an_image_is_chosen_but_not_uploaded(
    page, component_origin
):
    """On Campaign images a chosen file is a change: Continue waits (#1404).

    Pressing Continue then would drop the chosen image unsaid and record
    the step kept as is, so Continue is held with its hint, and nothing
    in its row moves, until the file is cleared.
    """
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.goto(component_origin + ARTWORK_STEP)
    proceed = page.get_by_role("button", name="Continue to Go-live readiness")
    hint = page.locator("#campaign-continue-hint")
    available(proceed)
    assert not hint.is_visible()
    row = page.locator("[data-campaign-step-actions]")
    before = proceed.bounding_box()["y"] - row.bounding_box()["y"]
    field = slot(page, "Banner (top of the Family emails only)").get_by_label("Image")
    field.set_input_files(png())
    greyed(proceed)
    visible(hint)
    assert proceed.get_attribute("aria-describedby") == "campaign-continue-hint"
    assert proceed.bounding_box()["y"] - row.bounding_box()["y"] == before
    field.set_input_files([])
    available(proceed)
    assert not hint.is_visible()
    assert failures == []


@pytest.mark.parametrize("width", [320, 1280])
def test_load_step_offers_the_load_then_continue_in_place(
    page, component_origin, axe_source, width
):
    """Load ParishSoft data's primary is the load, then Continue (#1404 PR 5).

    Before a load the page's own button is the one primary and the action
    row has Back only. Requesting the load answers in place: the action row
    gains Continue to Share options and the page's button becomes a
    secondary Refresh now in the same place and at the same width, so the
    pointer stays on it, with no reload.
    """
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(answer_load(page, component_origin, LOAD))
    page.evaluate(MARK)
    stepper = page.locator("#campaign-stepper")
    assert "Step 2 of 8: Load ParishSoft data" in stepper.inner_text()
    visible(page.locator("[data-campaign-load-intro]"))
    assert page.locator("[data-campaign-load]").count() == 0
    row = page.locator("[data-campaign-step-actions]")
    back = row.locator("[data-campaign-step-back]")
    assert back.get_attribute("href") == reverse("admin:campaign_settings")
    assert row.get_by_role("link", name="Continue to Share options").count() == 0
    start = page.get_by_role("button", name="Load the campaign's ParishSoft data")
    visible(start)
    assert "secondary" not in (start.get_attribute("class") or "")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert axe_violations(page, axe_source) == []
    start.scroll_into_view_if_needed()
    before = start.bounding_box()
    start.click()
    proceed = row.get_by_role("link", name="Continue to Share options")
    visible(proceed)
    assert proceed.get_attribute("href") == reverse("admin:share_settings")
    refresh = page.get_by_role("button", name="Refresh now")
    visible(refresh)
    assert "secondary" in refresh.get_attribute("class")
    assert_same_box(before, refresh.bounding_box())
    assert page.evaluate(MARKED) == "kept"
    assert failures == []


def assert_same_box(before, after):
    """Assert a control kept its place and size, to within 1 px (#736)."""
    for key in ("x", "y", "width", "height"):
        assert abs(before[key] - after[key]) <= 1, (key, before, after)


def answer_load(page, origin, first):
    """Serve the step at the real refresh address; return that address.

    A load answers ``first`` and the form's in-place POST answers
    ``LOADING``, as the server would after recording the load (redirecting
    back to this same address): the page and its answer must share an
    address for the answer to be swapped in place.
    """
    address = origin + reverse("admin:source_refresh")

    def answer(route):
        """Serve ``first`` for a load and ``LOADING`` for the POST."""
        shown = LOADING if route.request.method == "POST" else first
        route.fulfill(response=route.fetch(url=origin + shown, method="GET"))

    page.route(lambda url: url.split("#")[0] == address, answer)
    return address


def test_failed_load_says_so_and_try_again_clears_it(page, component_origin):
    """After a failed load the step says so, with Try again (#1404 PR 5).

    The notice is in the run's region after the button, so Try again's
    in-place answer clears it, showing the load under way and Continue, with
    no reload and without moving the button under the pointer.
    """
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.goto(answer_load(page, component_origin, LOAD_FAILED))
    page.evaluate(MARK)
    notice = page.locator("[data-campaign-load-failed]")
    visible(notice)
    assert "The campaign's ParishSoft data did not load." in notice.inner_text()
    link = notice.get_by_role("link", name="See what went wrong")
    assert link.get_attribute("href") == reverse(
        "admin:background_task_page", args=[UUID(int=1404)]
    )
    row = page.locator("[data-campaign-step-actions]")
    assert row.get_by_role("link", name="Continue to Share options").count() == 0
    again = page.get_by_role("button", name="Try again")
    again.scroll_into_view_if_needed()
    before = again.bounding_box()
    again.click()
    visible(row.get_by_role("link", name="Continue to Share options"))
    hidden(notice)
    refresh = page.get_by_role("button", name="Refresh now")
    assert_same_box(before, refresh.bounding_box())
    assert page.locator("[data-campaign-load-failed]").count() == 0
    assert page.evaluate(MARKED) == "kept"
    assert failures == []
