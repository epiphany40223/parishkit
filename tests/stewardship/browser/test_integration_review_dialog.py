"""Integration settings reviewed and applied in the review dialog (#750).

Save without a new key opens the shared review dialog, a compact review
asking "Save the … settings?"; Apply closes it, redraws the editor's region
in place at the applied version and says what was applied in the status
line under the form, with no reload and no layout shift. Save with a new key
pasted is a confirmation too (#1568): its Review is posted without the key,
the dialog names the key by its last four characters only, and Apply posts
the page's own form once, without a page load; the key's install is then
followed on the page and redrawn in place when it finishes. Finish
switching to the new key is a confirmation in the same dialog. Remove
Slack is a confirmation. A field's error stays on the form. ParishSoft's
refresh schedule change keeps its full review, and the redrawn schedule
editor works again. Pages and answers are
``integration_dialog_components``'.
"""

import re

import pytest

from .integration_dialog_components import (
    NEW_CHANNEL,
    PARISHSOFT_KEY,
    PARISHSOFT_KEY_REVIEW,
    SCHEDULE,
    SCHEDULE_REVIEW,
    SLACK,
    SLACK_FINISH,
    SLACK_KEY_REVIEW,
    SLACK_KEYED,
    SLACK_PENDING,
    SLACK_REFUSED,
    SLACK_REMOVE,
    SLACK_REMOVING,
    SLACK_REVIEW,
    SLACK_STATUS,
    SLACK_STATUS_DONE,
    SLACK_STATUS_PENDING,
    SLACK_UNSELECTED,
)
from .refresh_schedule_components import BASIC, CHECK, check_answer
from .review_helpers import answer_reviews
from .test_in_place import MARK, MARKED, count_requests
from .waits import (
    available,
    eventually,
    has_attribute,
    hidden,
    recorded,
    same_place,
    visible,
)

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "webkit"], indirect=True
)
FOCUSED = "document.activeElement && document.activeElement.id"
DIGEST = "#integration-form input[name=base_digest]"
# Where an element sits on the page (not in the window, which may scroll).
PLACE = """(id) => {
    const box = document.getElementById(id).getBoundingClientRect();
    return [box.left + window.scrollX, box.top + window.scrollY];
}"""
SECRET = "xoxb-SYNTHETIC-SECRET-TOKEN"
# What the review dialog's body holds (emptied whenever the dialog closes).
REVIEW_BODY = (
    "document.querySelector('#integration-review [data-review-body]').textContent"
)


def watch(page):
    """Fail on any script error, and mark the page so a reload shows."""
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.evaluate(MARK)
    return failures


def answer(page, origin, address, action, fixture, status=200):
    """Answer ``address``'s POSTs of ``action`` with ``fixture``; let the
    others through. Returns the answered POST bodies."""
    bodies = []

    def handle(route):
        """Serve ``fixture`` for the matching POST only."""
        request = route.request
        if request.method != "POST" or f"action={action}" not in (
            request.post_data or ""
        ):
            route.fallback()
            return
        bodies.append(request.post_data)
        fetched = route.fetch(url=origin + fixture, method="GET")
        route.fulfill(response=fetched, status=status)

    page.route(lambda url: url.split("#")[0] == origin + address, handle)
    return bodies


def dialog_of(page, name):
    """The review dialog, open, by its accessible name."""
    dialog = page.get_by_role("dialog", name=name)
    visible(dialog)
    return dialog


def places(page):
    """Where the form and its status line sit on the page."""
    return [
        page.evaluate(PLACE, id) for id in ("integration-form", "integration-outcome")
    ]


def test_save_is_a_compact_review_applied_in_place(page, component_origin):
    """Save opens the compact review on its question; Apply closes it,
    redraws the form in place at the applied version and says what was
    applied, with focus on the status line. Nothing reloads, the form and
    the status line do not move, the page does not scroll, and the dialog
    never holds the key field or anything but the signed preview."""
    reviews = answer_reviews(page, component_origin, SLACK_REVIEW, address=SLACK)
    # Tall enough that the status line is in view, so focusing it never
    # needs to scroll: any scroll would be a jump.
    page.set_viewport_size({"width": 1280, "height": 1600})
    page.goto(component_origin + SLACK)
    failures = watch(page)
    posts = count_requests(page, "POST", SLACK)
    page.get_by_label("Slack channel ID").fill(NEW_CHANNEL)
    before, scrolled = places(page), page.evaluate("window.scrollY")
    page.get_by_role("button", name="Save", exact=True).click()
    dialog = dialog_of(page, "Save the Slack notifications settings?")
    assert page.evaluate(FOCUSED) == "integration-review-title"
    lines = dialog.locator("#integration-review-summary")
    assert lines.inner_text().startswith("Slack channel ID:")
    has_attribute(dialog, "aria-describedby", "integration-review-summary")
    # Only the signed preview travels with Apply; no key, no signed request.
    html = dialog.evaluate("node => node.outerHTML")
    for absent in ('name="candidate"', 'name="intent"', "synthetic-intent", SECRET):
        assert absent not in html
    carried = dialog.locator("form[data-review-apply] input[data-review-carried]")
    assert carried.evaluate_all("nodes => nodes.map((node) => node.name)") == [
        "preview"
    ]
    assert len(reviews) == 1 and f"channel_id={NEW_CHANNEL}" in reviews[0]
    dialog.get_by_role("button", name="Apply").click()
    hidden(dialog)
    eventually(
        page, "document.getElementById('integration-outcome').textContent !== ''"
    )
    line = page.locator("#integration-outcome")
    assert re.search(
        r"^Slack notifications settings changed\. Applied at \d{1,2}:\d{2}\s?[AP]M\.$",
        line.inner_text(),
    ), line.inner_text()
    assert page.evaluate(FOCUSED) == "integration-outcome"
    has_attribute(page.locator(DIGEST), "value", "b" * 64)
    assert page.get_by_label("Slack channel ID").input_value() == NEW_CHANNEL
    assert same_place(places(page), before)
    assert page.evaluate("window.scrollY") == scrolled
    assert page.url == component_origin + SLACK
    assert page.evaluate(MARKED) == "kept"
    assert len(posts) == 2
    assert not failures


def answer_key(page, origin, address, review, applied, reread=None):
    """Answer ``address``'s key Review (posted without the key) with
    ``review``, its Apply (the form with the key) with ``applied`` and, when
    given, an in-place re-read of the page (a fetch) with ``reread``; let
    everything else through. Returns the (reviews, applies) bodies, each
    with whether it was a page load."""
    reviews, applies = [], []

    def handle(route):
        """Serve the matching fixture for the key's Review or Apply."""
        request = route.request
        body = request.post_data or ""
        if reread and request.method == "GET" and request.resource_type != "document":
            route.fulfill(response=route.fetch(url=origin + reread, method="GET"))
            return
        if request.method != "POST" or "action=preview" not in body:
            route.fallback()
            return
        sent = reviews if "key_review=yes" in body else applies
        sent.append((body, request.is_navigation_request()))
        fixture = review if sent is reviews else applied
        route.fulfill(response=route.fetch(url=origin + fixture, method="GET"))

    page.route(lambda url: url.split("#")[0] == origin + address, handle)
    return reviews, applies


def answer_in_turn(page, origin, address, *fixtures):
    """Answer each fetch of ``address`` with the next of ``fixtures`` (the
    last from then on), never the page's own load. Returns the reads."""
    reads = []

    def handle(route):
        """Serve the next fixture to a fetch; let a page load through."""
        if route.request.resource_type == "document":
            route.fallback()
            return
        reads.append(route.request.url)
        fixture = fixtures[min(len(reads), len(fixtures)) - 1]
        route.fulfill(response=route.fetch(url=origin + fixture, method="GET"))

    page.route(lambda url: url.split("#")[0] == origin + address, handle)
    return reads


def test_a_pasted_key_is_confirmed_and_installed_in_place(page, component_origin):
    """Save with a key pasted opens the key's confirmation on its title,
    naming the key by its last four characters only; the Review never
    carries the key, and Cancel keeps it in its field. Apply posts the page's
    own form once, key included, without a page load; the dialog closes,
    its body emptied (after Cancel too, so the last four do not linger),
    the status line says the key is being installed, and once the check
    finishes the status line and editor are redrawn in place (no reload, no
    scroll), Key updated, with the key gone from the page (#1568)."""
    reviews, applies = answer_key(
        page, component_origin, SLACK, SLACK_KEY_REVIEW, SLACK_PENDING, SLACK_KEYED
    )
    polls = answer_in_turn(
        page, component_origin, SLACK_STATUS, SLACK_STATUS_PENDING, SLACK_STATUS_DONE
    )
    page.set_viewport_size({"width": 1280, "height": 1600})
    page.goto(component_origin + SLACK)
    failures = watch(page)
    token = page.locator("#id_candidate")
    token.fill(SECRET)
    save = page.get_by_role("button", name="Save", exact=True)
    save.click()
    dialog = dialog_of(page, "Replace the Slack bot token?")
    assert page.evaluate(FOCUSED) == "integration-review-title"
    has_attribute(dialog, "aria-describedby", "integration-review-summary")
    assert "ending in OKEN" in dialog.inner_text()
    html = dialog.evaluate("node => node.outerHTML")
    assert SECRET not in html and "SYNTHETIC" not in html
    assert len(reviews) == 1 and not reviews[0][1]
    assert "candidate" not in reviews[0][0] and "SYNTHETIC" not in reviews[0][0]
    dialog.get_by_role("button", name="Cancel").click()
    hidden(dialog)
    # The dialog's close listener empties the body a task after close().
    eventually(page, REVIEW_BODY, "")
    assert token.input_value() == SECRET and applies == []
    save.click()
    dialog = dialog_of(page, "Replace the Slack bot token?")
    reviewed = page.evaluate("window.scrollY")
    dialog.get_by_role("button", name="Apply").click()
    hidden(dialog)
    visible(page.get_by_text("Installing the new key"))
    assert len(applies) == 1
    body, navigated = applies[0]
    assert not navigated and f"candidate={SECRET}" in body
    visible(page.get_by_text("Key updated."))
    eventually(page, REVIEW_BODY, "")
    assert page.locator("input[name=candidate]").count() == 1
    assert page.locator("input[name=candidate]").input_value() == ""
    assert SECRET not in page.content()
    assert len(polls) >= 2
    assert page.evaluate("window.scrollY") == reviewed
    assert page.url == component_origin + SLACK
    assert page.evaluate(MARKED) == "kept"
    assert len(applies) == 1
    assert not failures


def test_parishsofts_key_sits_before_its_schedule(page, component_origin):
    """ParishSoft draws the organization ID, the new key, then the refresh
    schedule editor, Save, and the schedule's outlook below the form, so the
    key never splits the schedule's entry from its display; its key's
    confirmation names the key by its last four characters and lists the
    other setting saved with it (#1568)."""
    reviews, _ = answer_key(
        page, component_origin, PARISHSOFT_KEY, PARISHSOFT_KEY_REVIEW, PARISHSOFT_KEY
    )
    page.goto(component_origin + PARISHSOFT_KEY)
    failures = watch(page)
    # Top to bottom on the page, as drawn.
    tops = [
        page.locator(selector).first.evaluate(
            "node => node.getBoundingClientRect().top + window.scrollY"
        )
        for selector in (
            "#id_organization_id",
            "input[name=candidate]",
            "[data-schedule-part=status]",
            "#refresh-schedule-outlook",
        )
    ]
    assert tops == sorted(tops) and len(set(tops)) == len(tops), tops
    page.locator("input[name=candidate]").fill("synthetic-parishsoft-key-9876")
    page.get_by_role("button", name="Save", exact=True).click()
    dialog = dialog_of(page, "Replace the ParishSoft key?")
    text = dialog.inner_text()
    assert "ending in 9876" in text and "synthetic-parishsoft" not in text
    assert "Expected ParishSoft organization ID:" in text
    assert "checked with ParishSoft in the background" in text
    assert len(reviews) == 1 and "candidate" not in reviews[0][0]
    assert not failures


def test_finish_switching_is_confirmed_in_the_dialog(page, component_origin):
    """Finish switching to the new key opens its confirmation on the
    settings page (#1568); Apply posts its signed preview and key change,
    follows the change, then redraws the status line and editor in place:
    the error line and its button are gone, and the status line says the
    switch was applied."""
    finishes = answer(page, component_origin, SLACK_UNSELECTED, "finish", SLACK_FINISH)
    page.set_viewport_size({"width": 1280, "height": 1600})
    page.goto(component_origin + SLACK_UNSELECTED)
    failures = watch(page)
    page.get_by_role("button", name="Finish switching to the new key").click()
    dialog = dialog_of(page, "Finish switching to the new key?")
    assert page.evaluate(FOCUSED) == "integration-review-title"
    assert "is stopped until you finish switching" in dialog.inner_text()
    carried = dialog.locator("form[data-review-apply] input[data-review-carried]")
    assert carried.evaluate_all("nodes => nodes.map((node) => node.name)") == [
        "preview",
        "request_id",
    ]
    assert len(finishes) == 1
    dialog.get_by_role("button", name="Apply").click()
    hidden(dialog)
    eventually(
        page, "document.getElementById('integration-outcome').textContent !== ''"
    )
    assert (
        page.locator("#integration-outcome")
        .inner_text()
        .startswith("Slack notifications switched to its new key. Applied at")
    )
    finish = page.get_by_role("button", name="Finish switching to the new key")
    assert finish.count() == 0
    has_attribute(page.locator(DIGEST), "value", "b" * 64)
    assert page.evaluate(MARKED) == "kept"
    assert not failures


def test_remove_slack_is_a_confirmation_redrawn_in_place(page, component_origin):
    """Remove Slack asks "Remove Slack?" with one sentence describing the
    dialog; Apply redraws the editor in place as not set up, and the status
    line, where the form ends, says Slack was removed."""
    removals = answer(page, component_origin, SLACK_REMOVING, "remove", SLACK_REMOVE)
    page.goto(component_origin + SLACK_REMOVING)
    failures = watch(page)
    page.get_by_role("button", name="Remove Slack").click()
    dialog = dialog_of(page, "Remove Slack?")
    has_attribute(dialog, "aria-describedby", "integration-review-summary")
    assert "Slack alerts stop at once; to set Slack up again" in dialog.inner_text()
    assert len(removals) == 1
    form = page.evaluate(PLACE, "integration-form")
    dialog.get_by_role("button", name="Apply").click()
    hidden(dialog)
    eventually(
        page, "document.getElementById('integration-outcome').textContent !== ''"
    )
    assert (
        page.locator("#integration-outcome")
        .inner_text()
        .startswith("Slack removed. Applied at")
    )
    visible(page.get_by_text("Slack is not set up."))
    assert page.get_by_role("button", name="Remove Slack").count() == 0
    has_attribute(page.locator(DIGEST), "value", "b" * 64)
    # The not-set-up line is drawn above the form, so the form may move
    # down once, by that line; it never moves up under the reader.
    assert page.evaluate(PLACE, "integration-form")[1] >= form[1]
    assert page.evaluate(MARKED) == "kept"
    assert not failures


def test_a_field_error_stays_on_the_form(page, component_origin):
    """A Save refused for a field's value keeps the dialog shut: the field
    is marked with its message and the reader's typing stays."""
    answer_reviews(page, component_origin, SLACK_REFUSED, 400, address=SLACK)
    page.goto(component_origin + SLACK)
    failures = watch(page)
    field = page.get_by_label("Slack channel ID")
    # The browser's own pattern admits it; the server refuses it.
    field.fill("CREFUSED")
    page.get_by_role("button", name="Save", exact=True).click()
    has_attribute(field, "aria-invalid", "true")
    visible(page.locator("#id_channel_id_error"))
    assert not page.locator("dialog#integration-review").is_visible()
    assert field.input_value() == "CREFUSED"
    assert page.evaluate(MARKED) == "kept"
    assert not failures


def test_a_schedule_change_keeps_its_full_review_and_the_editor_works_after(
    page, component_origin
):
    """A refresh schedule change keeps its full review under the dialog's
    own title. Once applied, the editor's region is redrawn in place, and
    the redrawn schedule editor checks a change live again, once."""
    from .conftest import NOW

    checks = []

    def check(route):
        """Answer the live check with the server's own code."""
        checks.append(route.request.post_data or "")
        route.fulfill(
            status=200,
            content_type="text/html; charset=utf-8",
            body=check_answer(BASIC, route.request.post_data, NOW),
        )

    page.route(f"**{CHECK}", check)
    answer_reviews(page, component_origin, SCHEDULE_REVIEW, address=SCHEDULE)
    page.goto(component_origin + SCHEDULE)
    failures = watch(page)
    page.get_by_role("radio", name=re.compile("^Every")).check()
    save = page.get_by_role("button", name="Save", exact=True)
    available(save)
    save.click()
    dialog = dialog_of(page, "Review the ParishSoft settings")
    visible(dialog.get_by_role("heading", name="Refresh schedule changes"))
    assert "Full refreshes removed: 02:00." in dialog.inner_text()
    dialog.get_by_role("button", name="Apply").click()
    hidden(dialog)
    eventually(
        page, "document.getElementById('integration-outcome').textContent !== ''"
    )
    assert (
        page.locator("#integration-outcome")
        .inner_text()
        .startswith("ParishSoft settings changed. Applied at")
    )
    has_attribute(page.locator(DIGEST), "value", "b" * 64)
    # The redrawn editor is wired: a new choice is checked live, once.
    seen = len(checks)
    page.get_by_role("radio", name="Nightly only").check()
    recorded(page, checks, seen + 1)
    # And only the redrawn editor: the one it replaced asks nothing more.
    page.wait_for_timeout(800)
    assert len(checks) == seen + 1
    assert page.evaluate(MARKED) == "kept"
    assert not failures


def test_spaces_the_server_strips_are_no_key_on_the_page(page, component_origin):
    """A key field holding only what the server strips (U+0085, U+3000) is
    no key on the page either: Save opens the settings' review dialog, as the
    server reviews it (#750); U+FEFF, which the server keeps, is a key, so
    Save opens the key's confirmation, its Review posted without it (#1568)."""
    reviews = answer_reviews(page, component_origin, SLACK_REVIEW, address=SLACK)
    page.goto(component_origin + SLACK)
    page.get_by_label("Slack channel ID").fill(NEW_CHANNEL)
    token = page.get_by_label("Slack bot token")
    token.fill("\x85\u3000")
    page.get_by_role("button", name="Save", exact=True).click()
    dialog = dialog_of(page, "Save the Slack notifications settings?")
    assert len(reviews) == 1
    dialog.get_by_role("button", name="Cancel").click()
    hidden(dialog)
    page.unroute_all()
    keyed, _ = answer_key(page, component_origin, SLACK, SLACK_KEY_REVIEW, SLACK)
    token.fill("\ufeff")
    page.get_by_role("button", name="Save", exact=True).click()
    dialog_of(page, "Replace the Slack bot token?")
    (body, navigated) = keyed[0]
    assert not navigated and "key_review=yes" in body and "candidate" not in body
