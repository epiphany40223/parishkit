"""Long setup field help opens from the "i" button beside the field's label."""

import re

import pytest

from .waits import visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)


def test_long_field_help_opens_from_its_tip(page, component_origin):
    """The hint stays visible; the full help opens, describes the field, and closes."""
    from playwright.sync_api import expect

    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.goto(component_origin + "/setup-credential")
    field = page.get_by_label("ParishSoft API key")
    visible(page.get_by_text("Paste it exactly. It is never displayed again."))
    tip_id = field.get_attribute("id") + "_tip"
    bubble = page.locator(f"#{tip_id}")
    expect(bubble).to_be_hidden()
    # Screen readers still hear the full help from the closed bubble.
    assert tip_id in field.get_attribute("aria-describedby").split()
    expect(field).to_have_accessible_description(
        re.compile(r"^Paste it exactly\. It is never displayed again\. .*encrypted")
    )

    button = page.locator(f'button[aria-controls="{tip_id}"]')
    button.click()
    visible(bubble)
    expect(bubble).to_contain_text("stored encrypted")
    expect(button).to_have_attribute("aria-expanded", "true")
    page.keyboard.press("Escape")
    expect(bubble).to_be_hidden()
    assert not failures
