"""Ordinary form submissions show a busy button and ignore repeated clicks."""

import pytest

from .conftest import NOW

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

# A plain form like many admin pages have, added after load so the shared
# script's document-level handling (not a per-form setup) is what is tested.
PROBE = """(action) => {
    const form = document.createElement('form');
    form.method = 'post';
    form.action = action;
    form.id = 'probe';
    form.innerHTML = '<input name="note" value="kept">'
        + '<button type="submit" name="action" value="first">First</button>'
        + '<button type="submit" name="action" value="second">Second</button>';
    document.querySelector('main').append(form);
}"""


def answered(page, pattern):
    """Record POSTs to ``pattern`` and answer 204, which leaves the page as is.

    A 204 keeps the submitting page (and its busy state) on screen without a
    navigation left hanging, standing in for a slow server response.
    """
    requests = []

    def handle(route):
        """Count the submission; a GET would be an unexpected navigation."""
        requests.append(route.request)
        route.fulfill(status=204)

    page.route(pattern, handle)
    return requests


def test_double_click_submits_once_and_keeps_the_submitter_value(
    page, component_origin
):
    """A repeat click is ignored, and the clicked button's name/value is sent."""
    page.goto(component_origin + "/setup-parish")
    page.evaluate(PROBE, "/submit-probe")
    requests = answered(page, "**/submit-probe")
    page.get_by_role("button", name="Second").dblclick()
    # Playwright treats aria-disabled as disabled; force the click a person
    # could still make, which the page must ignore.
    page.get_by_role("button", name="First").click(force=True)
    page.keyboard.press("Enter")
    page.wait_for_timeout(300)
    assert len(requests) == 1
    assert requests[0].post_data == "note=kept&action=second"
    second = page.get_by_role("button", name="Second")
    assert "is-busy" in second.get_attribute("class")
    assert second.get_attribute("aria-disabled") == "true"
    assert page.get_by_role("button", name="First").get_attribute("aria-disabled")
    assert page.locator("#probe").get_attribute("aria-busy") == "true"
    assert page.get_by_role("status").filter(has_text="Working").count() == 1
    # Busy is a visible change, not only an attribute.
    colors = second.evaluate("node => getComputedStyle(node).backgroundColor")
    assert colors != page.locator(".setup-actions > button").evaluate(
        "node => getComputedStyle(node).backgroundColor"
    )


def test_a_response_that_does_not_navigate_releases_the_form(page, component_origin):
    """A download (or any reply that keeps this page) must not leave it stuck.

    The page cannot tell a download from any other response that leaves it in
    place, so a 204 stands in for one: Playwright's WebKit replaces the page
    for a routed attachment response, which a real browser download does not.
    """
    page.clock.install(time=NOW)
    page.goto(component_origin + "/setup-parish")
    page.evaluate(PROBE, "/download-probe")
    served = answered(page, "**/download-probe")
    first = page.get_by_role("button", name="First")
    first.click()
    page.wait_for_timeout(300)
    assert len(served) == 1
    assert "is-busy" in first.get_attribute("class")
    page.clock.fast_forward(9_000)
    assert first.get_attribute("aria-disabled") == "true"
    page.clock.fast_forward(1_000)
    assert "is-busy" not in (first.get_attribute("class") or "")
    assert first.get_attribute("aria-disabled") is None
    first.click()
    page.wait_for_timeout(300)
    assert len(served) == 2


def test_back_forward_cache_restore_and_script_owned_forms(page, component_origin):
    """A restored page is usable again; forms driven by script are untouched."""
    page.goto(component_origin + "/setup-parish")
    page.evaluate(PROBE, "/submit-probe")
    requests = answered(page, "**/submit-probe")
    page.get_by_role("button", name="First").click()
    page.wait_for_timeout(200)
    page.evaluate(
        "window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted: true}))"
    )
    first = page.get_by_role("button", name="First")
    assert first.get_attribute("aria-disabled") is None
    assert page.locator("#probe").get_attribute("aria-busy") is None
    # A form whose own handler takes over the submit is never marked busy.
    page.evaluate("""() => {
        const form = document.querySelector('#probe');
        form.addEventListener('submit', (event) => event.preventDefault());
    }""")
    first.click()
    first.click()
    assert first.get_attribute("aria-disabled") is None
    assert len(requests) == 1


OLD_KEY = "11111111-1111-4111-8111-111111111111"


def test_waiting_forms_stay_busy_and_explain_a_slow_server(page, component_origin):
    """Export queueing may wait behind a refresh; it must never look ignored.

    A data-submit-waits form stays busy far past the ordinary ten seconds, says
    "Still working" after a few seconds, and only after three minutes gives up
    with a visible message. A page restored from the back/forward cache gets a
    fresh one-time request key, so a second export is not refused as a reuse.
    """
    page.clock.install(time=NOW)
    page.goto(component_origin + "/setup-parish")
    page.evaluate(PROBE, "/export-probe")
    page.evaluate(
        """(key) => {
        const form = document.querySelector('#probe');
        form.setAttribute('data-submit-waits', '');
        const input = document.createElement('input');
        Object.assign(input, {type: 'hidden', name: 'request_key', value: key});
        form.append(input);
    }""",
        OLD_KEY,
    )
    served = answered(page, "**/export-probe")
    first = page.get_by_role("button", name="First")
    first.click()
    page.wait_for_timeout(300)
    assert len(served) == 1
    page.clock.fast_forward(6_000)
    assert page.get_by_text("Still working").count() == 1
    page.clock.fast_forward(60_000)
    assert first.get_attribute("aria-disabled") == "true"
    page.clock.fast_forward(120_000)
    assert first.get_attribute("aria-disabled") is None
    assert page.get_by_text("No response from the server yet").count() == 1
    page.evaluate(
        "window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted: true}))"
    )
    key = page.locator('#probe input[name="request_key"]').input_value()
    assert key != OLD_KEY and len(key) == 36
    assert page.get_by_text("No response from the server yet").count() == 0


def test_restored_waiting_form_shows_no_stale_wait_note(page, component_origin):
    """A back/forward-cache restore before five seconds leaves no "Still working"."""
    page.clock.install(time=NOW)
    page.goto(component_origin + "/setup-parish")
    page.evaluate(PROBE, "/export-probe")
    page.evaluate(
        "() => document.querySelector('#probe').setAttribute('data-submit-waits', '')"
    )
    answered(page, "**/export-probe")
    page.get_by_role("button", name="First").click()
    page.wait_for_timeout(300)
    page.evaluate(
        "window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted: true}))"
    )
    page.clock.fast_forward(10_000)
    assert page.get_by_text("Still working").count() == 0
