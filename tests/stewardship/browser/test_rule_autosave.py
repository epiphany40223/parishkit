"""The autosave queue applies role ticks in order and only from applied receipts."""

import json
from urllib.parse import parse_qs

import pytest

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)
APPLIED = "b" * 64


def receipt(request_id, state, digest=None):
    """A request receipt as the status route answers."""
    return json.dumps(
        {
            "request_id": request_id,
            "state": state,
            "sequence": 1,
            "failure_code": "",
            "applied_digest": digest,
        }
    )


def serve(page, *, states, refuse=None):
    """Mock the apply and status routes; record every intent the page sends."""
    sent = []
    polled = []

    def apply(route, request):
        """Accept one intent with a fresh id, or refuse it by closed code."""
        body = parse_qs(request.post_data)
        sent.append({key: value[0] for key, value in body.items()})
        if refuse and refuse(sent[-1]):
            route.fulfill(
                status=400,
                content_type="application/json",
                body=json.dumps(
                    {
                        "errors": [
                            {"code": "invalid", "field_id": "intent", "message": "x"}
                        ]
                    }
                ),
            )
            return
        route.fulfill(
            status=202,
            content_type="application/json",
            body=receipt(f"request-{len(sent)}", "staged"),
        )

    def status(route, request):
        """Answer each poll from the scripted states for that request."""
        request_id = request.url.rsplit("/", 1)[-1]
        polled.append(request_id)
        queue = states.setdefault(request_id, ["applied"])
        state = queue.pop(0) if len(queue) > 1 else queue[0]
        route.fulfill(
            status=200,
            content_type="application/json",
            body=receipt(request_id, state, APPLIED if state == "applied" else None),
        )

    page.route("**/users/rules/apply", apply)
    page.route("**/users/rules/requests/*", status)
    return sent, polled


def test_ticks_autosave_in_order_and_adopt_the_applied_digest(page, component_origin):
    """Applied comes only from the receipt; the next intent waits for it."""
    sent, polled = serve(page, states={"request-1": ["staged", "prepared", "applied"]})
    page.goto(component_origin + "/portal-users")
    leader = page.get_by_role("row", name="leader@workspace.example", exact=False)
    # With scripts the review button is not how roles change.
    assert leader.get_by_role("button", name="Review role change").is_hidden()
    leader.get_by_label("Ministry leader").uncheck()
    assert leader.get_by_text("Applying", exact=False).is_visible()
    leader.get_by_label("Administrator").check()
    assert leader.get_by_text("Queued — not saved", exact=True).is_visible()
    leader.get_by_text("Applied", exact=True).wait_for()
    assert [item["role"] for item in sent] == ["ministry_leader", "administrator"]
    assert [item["checked"] for item in sent] == ["0", "1"]
    assert sent[0]["kind"] == "address"
    assert sent[0]["identity"] == "leader@workspace.example"
    assert sent[0]["base_digest"] == "0" * 64
    # The second intent was formed against the digest the first one applied.
    assert sent[1]["base_digest"] == APPLIED
    assert len({item["request_key"] for item in sent}) == 2
    assert polled[:3] == ["request-1", "request-1", "request-1"]
    assert page.locator('input[name="base_digest"]').first.input_value() == APPLIED
    # No intent remains, so leaving does not warn.
    page.close(run_before_unload=True)


def test_a_refusal_pauses_the_queue_and_leaving_warns(page, component_origin):
    """A refused intent restores its tick; later intents stay visibly unsaved."""
    sent, _ = serve(
        page, states={}, refuse=lambda intent: intent["role"] == "administrator"
    )
    page.goto(component_origin + "/portal-users")
    leader = page.get_by_role("row", name="leader@workspace.example", exact=False)
    # A plain click: the refusal restores the tick before check() could verify it.
    leader.get_by_label("Administrator").click()
    leader.get_by_text("Not saved", exact=False).wait_for()
    assert not leader.get_by_label("Administrator").is_checked()
    leader.get_by_label("Staff").uncheck()
    assert leader.get_by_text("Queued — not saved", exact=True).is_visible()
    assert len(sent) == 1
    page.on("dialog", lambda dialog: dialog.dismiss())
    with page.expect_event("dialog") as warned:
        page.close(run_before_unload=True)
    assert warned.value.type == "beforeunload"
