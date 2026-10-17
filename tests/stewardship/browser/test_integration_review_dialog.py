"""Integration settings reviewed and applied in the review dialog (#750).

Save without a new key opens the shared review dialog, a compact review
asking "Save the … settings?"; Apply closes it, redraws the editor's region
in place at the applied version and says what was applied in the status
line under the form, with no reload and no layout shift. Save with a new key
pasted submits natively instead (the slow key install is followed on the
page, the recorded exception), so no key ever reaches the dialog. Remove
Slack is a confirmation. A field's error stays on the form. ParishSoft's
refresh schedule change keeps its full review, and the redrawn schedule
editor works again. Pages and answers are
``integration_dialog_components``'.
"""

import re

import pytest

from .integration_dialog_components import (
    NEW_CHANNEL,
    SCHEDULE,
    SCHEDULE_REVIEW,
    SLACK,
    SLACK_REFUSED,
    SLACK_REMOVE,
    SLACK_REMOVING,
    SLACK_REVIEW,
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


def test_a_pasted_key_submits_natively_and_never_reaches_the_dialog(
    page, component_origin
):
    """Save with a new key pasted is the slow key install, the recorded
    exception: the form submits natively (a page load, as before), no
    review is requested, and the dialog never opens or holds the key."""
    page.goto(component_origin + SLACK)
    fetched = []
    page.on(
        "request",
        lambda request: (
            fetched.append(request)
            if request.method == "POST" and request.headers.get("x-requested-with")
            else None
        ),
    )
    page.evaluate(MARK)
    page.get_by_label("Slack bot token").fill(SECRET)
    dialog = page.locator("dialog#integration-review")
    with (
        page.expect_request(lambda request: request.method == "POST") as sent,
        page.expect_navigation(),
    ):
        page.get_by_role("button", name="Save", exact=True).click()
    # A page load (the form's own submission), not the review's fetch.
    assert sent.value.is_navigation_request()
    assert f"candidate={SECRET}" in sent.value.post_data
    assert fetched == []
    assert page.evaluate("document.querySelector('[data-mark]')") is None
    assert SECRET not in page.content()
    assert dialog.count() == 0 or not dialog.is_visible()


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
    no key on the page either: Save opens the review dialog, as the server
    reviews it (#750); U+FEFF, which the server keeps, is a key."""
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
    token.fill("\ufeff")
    with (
        page.expect_request(lambda request: request.method == "POST") as sent,
        page.expect_navigation(),
    ):
        page.get_by_role("button", name="Save", exact=True).click()
    # The form's own submission (a page load), not the review's fetch.
    assert sent.value.is_navigation_request()
