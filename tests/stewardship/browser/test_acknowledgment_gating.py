"""Primary actions wait for their required acknowledgment checkbox."""

import pytest

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

FINISH = "Check readiness and finish setup"


@pytest.mark.parametrize(
    "path,button",
    [
        ("/setup-confirmation", FINISH),
        ("/campaign-mail-unknown", "Send this test email"),
    ],
)
def test_submit_waits_for_the_acknowledgment(page, component_origin, path, button):
    """Muted and disabled until checked; unchecking disables it again."""
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.goto(component_origin + path)
    submit = page.get_by_role("button", name=button)
    checkbox = page.locator("input[data-acknowledgment]")
    assert submit.is_disabled()
    assert submit.evaluate("node => getComputedStyle(node).opacity") == "0.55"
    checkbox.check()
    assert submit.is_enabled()
    checkbox.uncheck()
    assert submit.is_disabled()
    checkbox.check()
    assert submit.is_enabled() and not failures


@pytest.mark.parametrize(
    "path,button",
    [
        ("/setup-confirmation-unready", FINISH),
        ("/campaign-mail-pending", "Send this test email"),
    ],
)
def test_checking_never_enables_a_button_the_server_disabled(
    page, component_origin, path, button
):
    """Unready setup or a pending test keeps its own disabled state."""
    page.goto(component_origin + path)
    submit = page.get_by_role("button", name=button)
    assert submit.is_disabled()
    boxes = page.locator("input[data-acknowledgment]")
    if boxes.count():
        boxes.first.check()
    assert submit.is_disabled()


def test_forms_without_an_acknowledgment_are_not_gated(page, component_origin):
    """A test with no uncertainty shows no prompt and can be sent at once."""
    page.goto(component_origin + "/campaign-mail")
    assert page.locator("input[data-acknowledgment]").count() == 0
    assert page.get_by_role("button", name="Send this test email").is_enabled()


def test_without_javascript_the_server_still_decides(browser_engine, component_origin):
    """Progressive enhancement: the button is enabled; the server validates."""
    context = browser_engine.new_context(java_script_enabled=False)
    try:
        page = context.new_page()
        page.goto(component_origin + "/setup-confirmation")
        assert page.get_by_role("button", name=FINISH).is_enabled()
    finally:
        context.close()
