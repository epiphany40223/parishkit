"""Families during maintenance: a friendly page, and a form that keeps its answers."""

import pytest

from .test_family_response import expect, form_payload, prepare, review

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)
MAINTENANCE = {"maintenance": True, "message": "Back by 3 PM."}
AXE = """async () => (await axe.run(document, {
  runOnly: {type: 'tag', values: ['wcag2a','wcag2aa','wcag21aa','wcag22aa']}
})).violations.map(v => v.id)"""


@pytest.mark.parametrize("width", [390, 1280])
def test_the_maintenance_page_is_friendly_and_accessible(
    page, component_origin, axe_source, width
):
    """One clear heading, the Administrator's note, and a way to check again."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + "/family-maintenance")
    expect(
        page.get_by_role("heading", name="Sorry, the site is temporarily unavailable")
    ).to_be_visible()
    expect(page.get_by_text("Back by 3 PM.")).to_be_visible()
    expect(page.get_by_role("link", name="Try again")).to_have_attribute("href", "/")
    page.evaluate(axe_source)
    assert page.evaluate(AXE) == []
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


@pytest.mark.parametrize("testing", [True, False])
def test_a_form_load_during_maintenance_offers_a_retry(page, component_origin, testing):
    """The note replaces a generic failure, and Try again loads the form later.

    Both modes load the form as soon as the page opens (#466), so the fallback
    is the same in Production and Testing.
    """
    attempts = []

    def load(route):
        """Closed for the first automatic load, open for the retry."""
        attempts.append(route.request)
        if len(attempts) == 1:
            route.fulfill(status=503, json=MAINTENANCE)
        else:
            route.fulfill(json={"form": form_payload(testing=testing)})

    prepare(page, component_origin, testing=testing, load=load)
    message = page.locator("#family-flow-message")
    expect(message).to_contain_text("temporarily closed for maintenance")
    expect(message).to_contain_text("Back by 3 PM.")
    # No form was ever shown, so there are no answers to reassure about.
    expect(message).not_to_contain_text("answers")
    retry = page.get_by_role("button", name="Try again")
    expect(retry).to_be_visible()
    retry.click()
    expect(page.locator("[data-step-link]").first).to_be_attached()
    assert len(attempts) == 2


def test_a_submit_during_maintenance_keeps_the_answers(page, component_origin):
    """Nothing is in doubt: the Review page stays, and Submit works once reopened."""
    calls = []

    def submit(route):
        """Closed for the first Submit, accepted for the second."""
        calls.append(route.request.post_data_json)
        if len(calls) == 1:
            route.fulfill(status=503, json=MAINTENANCE)
        else:
            route.fulfill(json={"accepted": True})

    prepare(page, component_origin, submit=submit)
    review(page)
    button = page.get_by_role("button", name="Submit to Sample Parish")
    button.click()
    message = page.locator("#family-flow-message")
    expect(message).to_contain_text("your answers on this page are still here")
    expect(message).to_contain_text("so your session stays active")
    main = page.locator("main").inner_text()
    assert "may not have been received" not in main
    expect(button).to_be_enabled()
    button.click()
    expect(page.get_by_role("heading", name="Thank you!", exact=True)).to_be_visible()
    assert len(calls) == 2 and calls[0]["answers"] == calls[1]["answers"]


def test_a_session_ending_after_a_maintenance_refusal_says_nothing_was_submitted(
    page, component_origin
):
    """A refused Submit is not in doubt, so the expiry notice must not suggest it."""
    calls = []

    def submit(route):
        """Closed for the first Submit; the session has ended by the second."""
        calls.append(route.request)
        if len(calls) == 1:
            route.fulfill(status=503, json=MAINTENANCE)
        else:
            route.fulfill(status=403, json={})

    prepare(page, component_origin, submit=submit)
    review(page)
    button = page.get_by_role("button", name="Submit to Sample Parish")
    button.click()
    expect(page.locator("#family-flow-message")).to_contain_text("maintenance")
    button.click()
    notice = page.locator("#session-expired")
    expect(notice).to_be_visible()
    expect(notice).to_contain_text("Unsubmitted changes have not been saved.")
    expect(notice).not_to_contain_text("If you just submitted")
