"""The Campaign setup stepper's layout, links and accessibility (#142 slice 4)."""

import pytest
from django.urls import reverse

from .keyboard import tab_to_next
from .stepper_components import PATH, REVIEW
from .test_components import axe_violations
from .test_in_place import MARK, MARKED
from .test_settings_in_place import answer_reviews
from .waits import has_text, hidden, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)


@pytest.mark.parametrize("width", [320, 1280])
def test_campaign_stepper_is_compact_with_plain_back_and_next_links(
    page, component_origin, axe_source, width
):
    """A short summary with Back and Next links; the full list opens on demand."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + PATH)
    stepper = page.locator("#campaign-stepper")
    text = stepper.inner_text()
    assert "Step 2 of 7: Share options" in text
    assert "1 of 7 steps completed" in text
    # It sits above the page, under the breadcrumbs, and stays compact.
    assert stepper.bounding_box()["height"] < (300 if width == 320 else 170)
    back = stepper.locator("[data-campaign-step-back]")
    forward = stepper.locator("[data-campaign-step-next]")
    assert back.inner_text() == "Back: Campaign settings"
    assert back.get_attribute("href") == reverse("admin:campaign_settings")
    assert forward.inner_text() == "Next: Pages and emails"
    assert forward.get_attribute("href") == reverse("admin:content_catalog")
    # Back and Next are links, never a form that could save anything.
    assert stepper.locator("form, button").count() == 0
    assert page.locator("#campaign-stepper .setup-steps").is_hidden()
    stepper.locator("summary").focus()
    page.keyboard.press("Enter")
    visible(stepper.locator(".setup-steps"))
    current = stepper.locator('.setup-steps [aria-current="step"]')
    assert "Share options" in current.inner_text()
    assert "Completed" in stepper.locator(".setup-step-done").inner_text()
    tab_to_next(page)
    assert page.evaluate(
        "document.activeElement.closest('#campaign-stepper .setup-steps') !== null"
    )
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert axe_violations(page, axe_source) == []


def test_apply_in_the_review_dialog_turns_next_into_continue(page, component_origin):
    """Once the dialog's Apply is applied, the page's redraw marks the step.

    The dialog redraws every region from the page's own address, the
    stepper among them: Share options reads completed and Next becomes
    Continue to the next step, with no reload.
    """
    answer_reviews(page, component_origin, REVIEW, address=PATH)
    page.goto(component_origin + PATH)
    page.evaluate(MARK)
    stepper = page.locator("#campaign-stepper")
    assert stepper.locator("[data-campaign-continue]").count() == 0
    page.locator("#id_options-0-label").fill("Renamed option")
    page.get_by_role("button", name="Review changes").click()
    dialog = page.get_by_role("dialog", name="Review Share options")
    dialog.get_by_role("button", name="Apply").click()
    hidden(dialog)
    has_text(stepper.locator(".setup-stepper-count"), "2 of 7 steps completed")
    proceed = stepper.locator("[data-campaign-continue]")
    visible(proceed)
    assert proceed.inner_text() == "Continue to the next step: Pages and emails"
    assert proceed.get_attribute("href") == reverse("admin:content_catalog")
    assert page.evaluate(MARKED)
