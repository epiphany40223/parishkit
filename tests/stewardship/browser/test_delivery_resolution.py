"""Evidence forms and durable warning behavior in all supported browser engines."""

from urllib.parse import parse_qs

import pytest

from .conftest import NOW

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)


def test_keyboard_resend_requires_evidence_and_explicit_duplicate_ack(
    page, component_origin
):
    """Native validation and keyboard submission preserve closed form identity."""
    page.goto(component_origin + "/delivery")
    assert "acceptance-help" in page.locator("#evidence-accept").get_attribute(
        "aria-describedby"
    )
    assert "mark_unknown" not in page.locator("main").inner_text()
    form = page.locator("form").filter(
        has=page.locator('[name="action"][value="resend"]')
    )
    form.locator("textarea").fill("Confirmed with the Family")
    assert not form.evaluate("form => form.checkValidity()")
    checkbox = form.get_by_role("checkbox")
    checkbox.focus()
    page.keyboard.press("Space")
    assert form.evaluate("form => form.checkValidity()")
    page.route(
        "**/admin/deliveries/*/resolve",
        lambda route: route.fulfill(status=200, body="Recorded"),
    )
    form.get_by_role("button").focus()
    with page.expect_request("**/admin/deliveries/*/resolve") as sent:
        page.keyboard.press("Enter")
    assert sent.value.method == "POST"
    fields = parse_qs(sent.value.post_data)
    assert set(fields) == {
        "csrfmiddlewaretoken",
        "command_id",
        "expected_version",
        "action",
        "note",
        "duplicate_acknowledged",
    }
    assert fields["duplicate_acknowledged"] == ["yes"]
    assert "Confirmed" not in sent.value.url


def test_warning_polls_durable_count_and_survives_unavailable_response(
    page, component_origin
):
    """Failed polling never hides uncertainty or turns a GET into session activity."""
    page.clock.install(time=NOW)
    count = [1234]
    requests = []

    def result(route):
        """Use only operational counts; never provide delivery details in polling."""
        requests.append(route.request)
        if count[0] is None:
            route.fulfill(status=503, body="Unavailable")
        else:
            route.fulfill(
                json={
                    "counts": dict(
                        queued=99 if count[0] == "bad" else 0,
                        running=0,
                        retry_wait=0,
                        abandoned=0,
                        active=0,
                    ),
                    "delivery_unknown": count[0],
                }
            )

    page.route("**/admin/background/counts", result)
    page.goto(component_origin + "/delivery")
    warning = page.locator("[data-delivery-warning]")
    assert warning.locator("..").get_attribute("aria-live") == "polite"
    for index, value in enumerate((1234, 1234, None, "bad", 0)):
        count[0] = value
        with page.expect_response("**/admin/background/counts"):
            page.clock.fast_forward(30000)
        if value == 0:
            assert warning.is_hidden()
        else:
            assert warning.is_visible()
            assert "1,234" in warning.inner_text()
        if value == "bad":
            assert page.locator("[data-background-total]").inner_text() == "0"
        if index == 0:
            warning.evaluate("""element => {
                window.warningMutations = 0;
                new MutationObserver(records => {
                    window.warningMutations += records.length;
                })
                  .observe(element, {childList: true, subtree: true, attributes: true});
            }""")
        elif index == 1:
            assert page.evaluate("window.warningMutations") == 0
    assert len(requests) == 5 and all(request.method == "GET" for request in requests)
