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


ROTATED = "r" * 64


def receipt(request_id, state, digest=None, failure=""):
    """A request receipt as the status route answers, with a rotated token."""
    return json.dumps(
        {
            "request_id": request_id,
            "state": state,
            "sequence": 1,
            "failure_code": failure,
            "applied_digest": digest,
            "csrf_token": ROTATED,
        }
    )


def serve(
    page,
    *,
    states,
    refuse=None,
    stale=None,
    deny=False,
    drop_first=False,
    base_failures=0,
    outage=None,
    poll_failures=0,
    fast=False,
):
    """Mock the apply, status and base routes; record every intent sent.

    `outage` is a mutable set: while it holds "apply" every apply is dropped;
    `poll_failures` drops that many status polls; `fast` shortens the page's
    retry, poll and deadline timing through its test seams.
    """
    sent = []
    polled = []
    reads = []
    rules = {"address": {LEADER: ["ministry_leader", "staff"]}, "domain": {}}
    outage = set() if outage is None else outage

    if fast:

        def hasten(route, request):
            """Serve the page with short timing so slow paths run quickly."""
            response = route.fetch()
            route.fulfill(
                response=response,
                body=response.text().replace(
                    "data-rule-autosave ",
                    'data-rule-autosave data-poll-ms="100" data-retry-wait-ms="50"'
                    ' data-deadline-ms="2000" ',
                    1,
                ),
            )

        page.route("**/portal-users", hasten)

    def apply(route, request):
        """Accept one intent with a fresh id, or answer as the scenario says."""
        body = parse_qs(request.post_data)
        sent.append(
            {key: value[0] for key, value in body.items()}
            | {"csrf": request.headers.get("x-csrftoken")}
        )
        if (drop_first and len(sent) == 1) or "apply" in outage:
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
        if len(polled) <= poll_failures:
            route.abort()
            return
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
        """The current rules for the conflict view, after any scripted failures."""
        reads.append(request.url)
        if len(reads) <= base_failures:
            route.abort()
            return
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
    # The rotated token from the first answer was adopted everywhere.
    assert sent[0]["csrf"] == "a" * 64 and sent[1]["csrf"] == ROTATED
    assert page.locator('[name="csrfmiddlewaretoken"]').first.input_value() == ROTATED
    # Off, on, off while the withdrawal is pending: the value it confirms
    # needs no further request.
    leader.get_by_label("Staff").uncheck()
    leader.get_by_label("Staff").check()
    leader.get_by_label("Staff").uncheck()
    leader.get_by_text("Applied", exact=True).nth(2).wait_for()
    assert len(sent) == 3 and sent[2]["role"] == "staff"
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
    rules["address"]["admin@example.org"] = ["administrator", "staff"]
    leader = leader_row(page, component_origin)
    leader.get_by_label("Administrator").check()
    panel = page.get_by_role("alert")
    panel.get_by_text("now: staff", exact=False).wait_for()
    # The row itself says the change was not applied.
    assert leader.get_by_text("Not saved: the rules changed", exact=False).is_visible()
    # A newer change while the view is open joins the same ordered list.
    leader.get_by_label("Ministry leader").uncheck()
    assert panel.get_by_role("listitem").count() == 2
    # The withdrawal already matches the current rules, so it is not preselected.
    picks = panel.get_by_role("checkbox")
    assert picks.nth(0).is_checked() and not picks.nth(1).is_checked()
    # A selection survives the redraw a further change causes.
    picks.nth(0).uncheck()
    picks.nth(1).check()
    leader.get_by_label("Staff").uncheck()
    picks = panel.get_by_role("checkbox")
    assert not picks.nth(0).is_checked() and picks.nth(1).is_checked()
    picks.nth(0).check()
    picks.nth(1).uncheck()
    page.get_by_role("button", name="Retry selected against current rules").click()
    leader.get_by_text("Applied", exact=True).nth(1).wait_for()
    assert [item["role"] for item in sent] == [
        "administrator",
        "administrator",
        "staff",
    ]
    assert sent[0]["request_key"] != sent[1]["request_key"]
    assert sent[1]["base_digest"] == CURRENT and sent[2]["base_digest"] == APPLIED
    # The discarded withdrawal shows the current rules' value, and an untouched
    # row was reconciled with the current rules as well.
    assert not leader.get_by_label("Ministry leader").is_checked()
    assert leader.get_by_text("Change discarded", exact=True).is_visible()
    admin = page.get_by_role("row", name="admin@example.org", exact=False)
    assert admin.get_by_label("Staff").is_checked()


def test_discarding_a_conflict_restores_current_rules(page, component_origin):
    """Discard all adopts the refreshed digest and the current values."""
    sent, _, rules = serve(
        page, states={}, stale=lambda intent, count: count == 1, base_failures=1
    )
    rules["address"][LEADER] = ["administrator", "ministry_leader", "staff"]
    leader = leader_row(page, component_origin)
    leader.get_by_label("Staff").uncheck()
    # The rules could not be read: the queue is kept and the read offered
    # again, with nothing to select against yet.
    panel = page.get_by_role("alert")
    page.get_by_role("button", name="Read the current rules again").wait_for()
    assert panel.get_by_role("listitem").count() == 1
    assert panel.get_by_role("checkbox").count() == 0
    page.get_by_role("button", name="Read the current rules again").click()
    page.get_by_role("button", name="Discard all").wait_for()
    # Once read, the current rules differ from the withdrawal, so it is
    # preselected for retry beside them.
    assert panel.get_by_text(
        "now: administrator, ministry_leader, staff", exact=False
    ).is_visible()
    assert panel.get_by_role("checkbox").is_checked()
    page.get_by_role("button", name="Discard all").click()
    assert leader.get_by_label("Staff").is_checked()
    assert leader.get_by_text("Change discarded", exact=True).is_visible()
    # Every control now shows the current rules, not the page's first render.
    assert leader.get_by_label("Administrator").is_checked()
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
        page,
        states={"request-1": ["failed"], "request-2": ["staged"] * 8 + ["stale_base"]},
        fast=True,
    )
    leader = leader_row(page, component_origin)
    leader.get_by_label("Staff").uncheck()
    leader.get_by_text("Not saved: the change failed", exact=False).wait_for()
    leader.get_by_label("Administrator").check()
    assert leader.get_by_text("Queued — not saved", exact=True).is_visible()
    assert len(sent) == 1
    page.get_by_role("button", name="Continue with the remaining changes").click()
    # A newer click on the in-flight control while it awaits activation
    # supersedes it when the stale base opens the conflict view.
    leader.get_by_text("Applying", exact=False).wait_for()
    leader.get_by_label("Administrator").uncheck()
    panel = page.get_by_role("alert")
    page.get_by_role("button", name="Retry selected against current rules").wait_for()
    assert len(sent) == 2 and sent[1]["role"] == "administrator"
    assert panel.get_by_role("listitem").count() == 1
    assert panel.get_by_text("withdraw Administrator", exact=False).is_visible()
    # The withdrawal already matches the current rules, so it is not preselected.
    assert not panel.get_by_role("checkbox").is_checked()


def test_an_unanswered_request_is_kept_uncertain_with_its_key(page, component_origin):
    """Exhausted retries keep the key; Try again resends that very key."""
    outage = {"apply"}
    sent, _, _ = serve(page, states={}, outage=outage, fast=True)
    leader = leader_row(page, component_origin)
    leader.get_by_label("Staff").uncheck()
    leader.get_by_text("Not confirmed", exact=False).wait_for(timeout=15000)
    assert page.get_by_role("button", name="Try again").is_visible()
    assert len(sent) == 4 and len({item["request_key"] for item in sent}) == 1
    # Nothing further is sent while the outcome is unknown.
    leader.get_by_label("Administrator").check()
    assert leader.get_by_text("Queued — not saved", exact=True).is_visible()
    assert len(sent) == 4
    outage.clear()
    page.get_by_role("button", name="Try again").click()
    leader.get_by_text("Applied", exact=True).nth(1).wait_for()
    assert sent[4]["request_key"] == sent[0]["request_key"]
    assert sent[5]["role"] == "administrator"


def test_unreadable_outcomes_pause_and_resume_the_same_request(page, component_origin):
    """Poll failures show reconnecting, then pause; Try again reads the same id."""
    sent, polled, _ = serve(page, states={}, poll_failures=5, fast=True)
    leader = leader_row(page, component_origin)
    leader.get_by_label("Staff").uncheck()
    page.get_by_role("button", name="Try again").wait_for(timeout=15000)
    assert leader.get_by_text("Reconnecting", exact=False).is_visible()
    assert len(sent) == 1 and len(polled) == 5
    page.get_by_role("button", name="Try again").click()
    leader.get_by_text("Applied", exact=True).wait_for()
    assert set(polled) == {"request-1"} and len(sent) == 1


def test_the_pause_panel_discards_or_continues(page, component_origin):
    """Remaining changes are discarded to confirmed values, or the queue resumes."""
    sent, _, _ = serve(
        page, states={}, refuse=lambda intent: intent["role"] == "administrator"
    )
    leader = leader_row(page, component_origin)
    leader.get_by_label("Administrator").click()
    leader.get_by_text("Not saved: this change", exact=False).wait_for()
    leader.get_by_label("Staff").uncheck()
    page.get_by_role("button", name="Discard the remaining changes").click()
    assert leader.get_by_label("Staff").is_checked()
    assert leader.get_by_text("Change discarded", exact=True).is_visible()
    assert page.get_by_role("alert").is_hidden()
    # A refusal with nothing else waiting offers a plain Continue.
    leader.get_by_label("Administrator").click()
    page.get_by_role("button", name="Continue", exact=True).wait_for()
    page.get_by_role("button", name="Continue", exact=True).click()
    assert page.get_by_role("alert").is_hidden()
    leader.get_by_label("Ministry leader").uncheck()
    leader.get_by_text("Applied", exact=True).wait_for()
    assert [item["role"] for item in sent] == [
        "administrator",
        "administrator",
        "ministry_leader",
    ]


def test_reloading_from_a_failed_read_abandons_the_queue_without_warning(
    page, component_origin
):
    """Discard all and reload leaves the page without the unsaved-changes dialog."""
    sent, _, _ = serve(
        page, states={}, stale=lambda intent, count: True, base_failures=99
    )
    leader = leader_row(page, component_origin)
    leader.get_by_label("Staff").uncheck()
    page.get_by_role("button", name="Discard all and reload page").wait_for()
    dialogs = []
    page.on("dialog", lambda dialog: (dialogs.append(dialog.type), dialog.dismiss()))
    with page.expect_navigation():
        page.get_by_role("button", name="Discard all and reload page").click()
    assert dialogs == [] and len(sent) == 1
    assert leader_row(page, component_origin).get_by_label("Staff").is_checked()
