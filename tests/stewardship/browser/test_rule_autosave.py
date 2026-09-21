"""The autosave queue applies role ticks in order and only from applied receipts."""

import json
from urllib.parse import parse_qs

import pytest

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)
APPLIED = "b" * 64
CURRENT = "c" * 64
LEADER = "leader@workspace.example"


def receipt(request_id, state, digest=None, failure=""):
    """A request receipt as the status route answers."""
    return json.dumps(
        {
            "request_id": request_id,
            "state": state,
            "sequence": 1,
            "failure_code": failure,
            "applied_digest": digest,
        }
    )


def serve(page, *, states, refuse=None, stale=None, deny=False, drop_first=False):
    """Mock the apply, status and base routes; record every intent sent."""
    sent = []
    polled = []
    rules = {"address": {LEADER: ["ministry_leader", "staff"]}, "domain": {}}

    def apply(route, request):
        """Accept one intent with a fresh id, or answer as the scenario says."""
        body = parse_qs(request.post_data)
        sent.append({key: value[0] for key, value in body.items()})
        if drop_first and len(sent) == 1:
            route.abort()
            return
        if deny:
            route.fulfill(status=403, content_type="application/json", body="{}")
            return
        errors = {"errors": [{"code": "x", "field_id": "intent", "message": "x"}]}
        if stale and stale(sent[-1], len(sent)):
            route.fulfill(
                status=409, content_type="application/json", body=json.dumps(errors)
            )
            return
        if refuse and refuse(sent[-1]):
            route.fulfill(
                status=400, content_type="application/json", body=json.dumps(errors)
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
        failure = "stale_base" if state == "stale_base" else ""
        state = "failed" if state == "stale_base" else state
        route.fulfill(
            status=200,
            content_type="application/json",
            body=receipt(
                request_id, state, APPLIED if state == "applied" else None, failure
            ),
        )

    def base(route, request):
        """The current rules for the conflict view."""
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"digest": CURRENT, "rules": rules}),
        )

    page.route("**/users/rules/apply", apply)
    page.route("**/users/rules/requests/*", status)
    page.route("**/users/rules/base", base)
    return sent, polled, rules


def leader_row(page, component_origin):
    """Open the page and find the leader's row."""
    page.goto(component_origin + "/portal-users")
    return page.get_by_role("row", name=LEADER, exact=False)


def test_ticks_autosave_in_order_and_adopt_the_applied_digest(page, component_origin):
    """Applied comes only from the receipt; the next intent waits for it."""
    sent, polled, _ = serve(
        page, states={"request-1": ["staged", "prepared", "yaml_activated", "applied"]}
    )
    leader = leader_row(page, component_origin)
    # With scripts the review button is not how roles change.
    assert leader.get_by_role("button", name="Review role change").is_hidden()
    leader.get_by_label("Ministry leader").uncheck()
    assert leader.get_by_text("Applying", exact=False).is_visible()
    leader.get_by_label("Administrator").check()
    assert leader.get_by_text("Queued — not saved", exact=True).is_visible()
    # A change of mind back to the confirmed value drops the queued intent.
    leader.get_by_label("Administrator").uncheck()
    assert leader.get_by_text("Queued — not saved", exact=True).count() == 0
    leader.get_by_label("Administrator").check()
    leader.get_by_text("Applied", exact=True).nth(1).wait_for()
    assert [item["role"] for item in sent] == ["ministry_leader", "administrator"]
    assert [item["checked"] for item in sent] == ["0", "1"]
    assert sent[0]["kind"] == "address" and sent[0]["identity"] == LEADER
    assert sent[0]["base_digest"] == "0" * 64
    # The second intent waited for the receipt and used the digest it applied.
    assert polled[:4] == ["request-1"] * 4
    assert sent[1]["base_digest"] == APPLIED
    assert len({item["request_key"] for item in sent}) == 2
    assert page.locator('input[name="base_digest"]').first.input_value() == APPLIED
    assert leader.get_by_text("Applied", exact=True).count() == 2
    # No intent remains, so leaving does not warn.
    page.close(run_before_unload=True)


def test_a_refusal_pauses_the_queue_and_leaving_warns(page, component_origin):
    """A refused intent restores its tick; later intents stay visibly unsaved."""
    sent, _, _ = serve(
        page, states={}, refuse=lambda intent: intent["role"] == "administrator"
    )
    leader = leader_row(page, component_origin)
    # A plain click: the refusal restores the tick before check() could verify it.
    leader.get_by_label("Administrator").click()
    refused = leader.get_by_text("Not saved: this change", exact=False)
    refused.wait_for()
    assert not leader.get_by_label("Administrator").is_checked()
    leader.get_by_label("Staff").uncheck()
    assert leader.get_by_text("Queued — not saved", exact=True).is_visible()
    # The refusal for one role survives a queued change to another.
    assert refused.is_visible()
    assert len(sent) == 1
    assert page.get_by_role(
        "button", name="Continue with the remaining changes"
    ).is_visible()
    page.on("dialog", lambda dialog: dialog.dismiss())
    with page.expect_event("dialog") as warned:
        page.close(run_before_unload=True)
    assert warned.value.type == "beforeunload"


def test_a_conflict_shows_current_rules_and_retries_selected_intents_afresh(
    page, component_origin
):
    """A stale digest opens the conflict view; retries take new keys and digest."""
    sent, _, rules = serve(page, states={}, stale=lambda intent, count: count == 1)
    rules["address"][LEADER] = ["staff"]
    leader = leader_row(page, component_origin)
    leader.get_by_label("Administrator").check()
    panel = page.get_by_role("alert")
    panel.get_by_text("now: staff", exact=False).wait_for()
    # A newer change while the view is open joins the same ordered list.
    leader.get_by_label("Ministry leader").uncheck()
    assert panel.get_by_role("listitem").count() == 2
    # The withdrawal already matches the current rules, so it is not preselected.
    picks = panel.get_by_role("checkbox")
    assert picks.nth(0).is_checked() and not picks.nth(1).is_checked()
    page.get_by_role("button", name="Retry selected against current rules").click()
    leader.get_by_text("Applied", exact=True).wait_for()
    assert [item["role"] for item in sent] == ["administrator", "administrator"]
    assert sent[0]["request_key"] != sent[1]["request_key"]
    assert sent[1]["base_digest"] == CURRENT
    # The discarded withdrawal shows the current rules' value.
    assert not leader.get_by_label("Ministry leader").is_checked()
    assert leader.get_by_text("Change discarded", exact=True).is_visible()


def test_discarding_a_conflict_restores_current_rules(page, component_origin):
    """Discard all adopts the refreshed digest and the current values."""
    sent, _, rules = serve(page, states={}, stale=lambda intent, count: count == 1)
    rules["address"][LEADER] = ["administrator", "ministry_leader", "staff"]
    leader = leader_row(page, component_origin)
    leader.get_by_label("Staff").uncheck()
    page.get_by_role("button", name="Discard all").wait_for()
    page.get_by_role("button", name="Discard all").click()
    assert leader.get_by_label("Staff").is_checked()
    assert leader.get_by_text("Change discarded", exact=True).is_visible()
    # Later changes go against the refreshed digest.
    leader.get_by_label("Staff").uncheck()
    leader.get_by_text("Applied", exact=True).wait_for()
    assert sent[1]["base_digest"] == CURRENT and len(sent) == 2
    page.close(run_before_unload=True)


def test_lost_access_clears_the_page_and_offers_sign_in(page, component_origin):
    """A 403 ends the queue and removes the restricted tables."""
    sent, _, _ = serve(page, states={}, deny=True)
    leader = leader_row(page, component_origin)
    # A plain click: the row is gone before uncheck() could verify the tick.
    leader.get_by_label("Staff").click()
    page.get_by_role("link", name="Sign in again").wait_for()
    assert page.get_by_role("table").count() == 0
    assert page.get_by_label("Email address").count() == 0
    assert len(sent) == 1
    page.close(run_before_unload=True)


def test_a_lost_answer_is_retried_with_the_same_key(page, component_origin):
    """No answer means uncertainty: the same key is sent again, never a new one."""
    sent, _, _ = serve(page, states={}, drop_first=True)
    leader = leader_row(page, component_origin)
    leader.get_by_label("Staff").uncheck()
    leader.get_by_text("Applied", exact=True).wait_for(timeout=15000)
    assert len(sent) == 2 and sent[0]["request_key"] == sent[1]["request_key"]


def test_a_failed_request_pauses_and_a_stale_base_failure_opens_the_conflict(
    page, component_origin
):
    """Terminal failures pause; a stale base found at activation is a conflict."""
    sent, _, _ = serve(
        page, states={"request-1": ["failed"], "request-2": ["stale_base"]}
    )
    leader = leader_row(page, component_origin)
    leader.get_by_label("Staff").uncheck()
    leader.get_by_text("Not saved: the change failed", exact=False).wait_for()
    leader.get_by_label("Administrator").check()
    assert leader.get_by_text("Queued — not saved", exact=True).is_visible()
    assert len(sent) == 1
    page.get_by_role("button", name="Continue with the remaining changes").click()
    page.get_by_role("button", name="Retry selected against current rules").wait_for()
    assert len(sent) == 2 and sent[1]["role"] == "administrator"
    assert page.get_by_role("alert").get_by_role("listitem").count() == 1
