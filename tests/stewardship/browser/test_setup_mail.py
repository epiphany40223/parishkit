"""Passive readiness status, explicit uncertainty acknowledgement and safe errors."""

import pytest

from .conftest import NOW

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
                "label": "Delivery uncertain"
                if unknown
                else "Provider accepted the test",
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
    page.clock.install(time=NOW)
    requests = []

    def respond(route):
        """Record the request method without sending any mail."""
        requests.append(route.request)
        route.fulfill(json=status(unknown=unknown))

    page.route(f"**/admin/setup/{delivery_channel}-test/status", respond)
    page.goto(component_origin + f"/setup-{delivery_channel}-test")
    page.wait_for_function("() => !document.querySelector('[data-mail-send]').disabled")
    assert len(requests) == 1 and requests[0].method == "GET"
    assert requests[0].post_data is None
    checkbox = page.locator("[data-mail-uncertain] input")
    assert checkbox.evaluate("node => node.required") is unknown
    assert checkbox.is_visible() is unknown
    if unknown:
        assert not page.locator("[data-setup-mail] form").evaluate(
            "form => form.checkValidity()"
        )
        checkbox.check()
        assert page.locator("[data-setup-mail] form").evaluate(
            "form => form.checkValidity()"
        )
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
