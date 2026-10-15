"""The Campaign setup stepper's layout, action row and accessibility (#142, #1404)."""

import pytest
from django.urls import reverse

from .artwork_components import STEP as ARTWORK_STEP
from .stepper_components import CONTENT, EDITOR, PATH, REVIEW
from .test_artwork_dialog import png, slot
from .test_components import axe_violations, shown_step_list
from .test_in_place import MARK, MARKED
from .test_settings_in_place import answer_reviews
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
    assert "Step 2 of 7: Share options" in text
    assert "1 of 7 steps completed" in text
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
    assert back.get_attribute("href") == reverse("admin:campaign_settings")
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

    before = placed()
    field = page.locator("#id_options-0-label")
    saved = field.input_value()
    field.fill("Renamed option")
    greyed(proceed)
    visible(hint)
    assert proceed.get_attribute("aria-describedby") == "campaign-continue-hint"
    assert placed() == before
    # Undoing the change gives Continue back.
    field.fill(saved)
    available(proceed)
    assert not hint.is_visible() and proceed.get_attribute("aria-describedby") is None
    assert placed() == before
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
    page.get_by_role("button", name="Review changes").click()
    dialog = page.get_by_role("dialog", name="Review Share options")
    dialog.get_by_role("button", name="Apply").click()
    hidden(dialog)
    has_text(stepper.locator(".setup-stepper-count"), "2 of 7 steps completed")
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
    assert "Step 3 of 7: Pages and emails" in stepper.inner_text()
    assert "2 of 7 steps completed" in stepper.inner_text()
    page.goto(component_origin + EDITOR)
    assert "Step 3 of 7: Pages and emails" in stepper.inner_text()
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
