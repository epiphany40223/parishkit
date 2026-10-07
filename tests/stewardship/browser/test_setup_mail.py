"""Passive readiness status, explicit uncertainty acknowledgement and safe errors."""

import pytest

from .conftest import NOW
from .waits import hidden, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)


def status(*, pending=False, unknown=False, revision=2):
    """Return only the closed status metadata emitted by the HTTP endpoint."""
    return {
        "revision": revision,
        "pending": pending,
        "unknown": unknown,
        "items": [
            {
                "id": "synthetic-delivery",
                "state": "delivery_unknown" if unknown else "accepted",
                "label": "Not sure it arrived" if unknown else "Delivered",
                "created_at": NOW.isoformat(),
                "current": True,
            }
        ],
    }


@pytest.fixture(params=["mail", "slack"])
def delivery_channel(request):
    """Both readiness controls use the same passive, bounded browser contract."""
    return request.param


@pytest.mark.parametrize("unknown", [False, True])
def test_terminal_status_is_passive_and_never_resends(
    page, component_origin, unknown, delivery_channel
):
    """Terminal polling stops; uncertainty requires a deliberate checked form."""
    from playwright.sync_api import expect

    page.clock.install(time=NOW)
    requests = []

    def respond(route):
        """Record the request method without sending any mail."""
        requests.append(route.request)
        route.fulfill(json=status(unknown=unknown))

    page.route(f"**/admin/setup/{delivery_channel}-test/status", respond)
    page.goto(component_origin + f"/setup-{delivery_channel}-test")
    expect(page.locator("[data-mail-state]")).to_have_js_property(
        "textContent", status(unknown=unknown)["items"][0]["label"]
    )
    assert len(requests) == 1 and requests[0].method == "GET"
    assert requests[0].post_data is None
    checkbox = page.locator("[data-mail-uncertain] input")
    send = page.locator("[data-mail-send]").first
    assert checkbox.evaluate("node => node.required") is unknown
    assert checkbox.is_visible() is unknown
    # The uncertainty acknowledgment gates the send button until checked.
    assert send.is_disabled() is unknown
    if unknown:
        assert not page.locator("[data-setup-mail] form").evaluate(
            "form => form.checkValidity()"
        )
        checkbox.check()
        assert page.locator("[data-setup-mail] form").evaluate(
            "form => form.checkValidity()"
        )
        assert send.is_enabled()
    page.clock.fast_forward(60000)
    assert len(requests) == 1
    assert page.evaluate("localStorage.length + sessionStorage.length") == 0


@pytest.mark.parametrize("failure", ["revision", "unknown_row", "http"])
def test_unavailable_status_requires_reload_not_a_blind_send(
    page, component_origin, failure, delivery_channel
):
    """Stale or denied responses stop polling and leave the send button disabled."""
    page.clock.install(time=NOW)
    requests = []

    def respond(route):
        """Use malformed correlation, never malformed executable HTML."""
        requests.append(route.request)
        payload = status(revision=3 if failure == "revision" else 2)
        if failure == "unknown_row":
            payload["items"][0]["id"] = "another-delivery"
        route.fulfill(status=403 if failure == "http" else 200, json=payload)

    page.route(f"**/admin/setup/{delivery_channel}-test/status", respond)
    page.goto(component_origin + f"/setup-{delivery_channel}-test")
    page.locator("[data-mail-status-error]").wait_for(state="visible")
    assert page.locator("[data-mail-send]").is_disabled()
    page.clock.fast_forward(60000)
    assert len(requests) == 1


@pytest.mark.parametrize("width", [320, 1280])
def test_email_test_is_an_ordinary_step_that_continues_once_accepted(
    page, component_origin, axe_source, width
):
    """Back and Send share the standard row; acceptance swaps Send for Continue."""
    from .test_components import axe_violations

    page.set_viewport_size({"width": width, "height": 900})
    page.clock.install(time=NOW)
    page.route(
        "**/admin/setup/mail-test/status",
        lambda route: route.fulfill(json=status(pending=True) | {"items": []}),
    )
    page.goto(component_origin + "/setup-mail-test-step")
    row = page.locator(".setup-actions")
    send = row.get_by_role("button", name="Send test email")
    onward = row.locator("[data-mail-continue]")
    assert row.locator(".setup-back").is_visible() and send.is_visible()
    assert onward.is_hidden()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert axe_violations(page, axe_source) == []
    # The worker accepts the test of this revision; the next poll sees it.
    page.unroute("**/admin/setup/mail-test/status")
    page.route(
        "**/admin/setup/mail-test/status", lambda route: route.fulfill(json=status())
    )
    page.clock.fast_forward(5000)
    onward.wait_for(state="visible")
    hidden(send)
    assert onward.get_attribute("href") == "/admin/setup/confirm"
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert axe_violations(page, axe_source) == []


def test_accepted_email_test_offers_continue_and_another_send(page, component_origin):
    """After acceptance the primary action is Continue; sending again is secondary."""
    page.goto(component_origin + "/setup-mail-test-done")
    row = page.locator(".setup-actions")
    visible(row.get_by_role("link", name="Continue"))
    assert row.locator("button").count() == 0
    visible(page.get_by_role("button", name="Send another test"))
