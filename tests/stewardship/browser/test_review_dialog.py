"""The shared review dialog (#750; UX conventions, "Review dialog") on New
and Edit user and the Import Ministry leaders review, in real browsers.

Review posts the form without a page load, the form inert and busy until
the answer; an accepted review opens a modal dialog, focus on its title;
Escape and Cancel close it before Apply, focus back on the button that
opened it; once Apply is
pressed nothing dismisses it until the answer, and Apply goes once the
change is recorded; once applied, New user returns to Users and the import
redraws Users in place, each with the reserved status line. A field's error
stays on the form; any other refusal shows in the dialog. Pages and POST
answers are user_components'.
"""

import json
import re
from urllib.parse import parse_qs

import pytest

from .test_components import axe_violations
from .user_components import (
    APPLY,
    APPLY_REFUSAL,
    APPLY_REFUSED,
    EDIT,
    LIST,
    NEW,
    NEW_INVALID,
    NEW_PENDING,
    NEW_REFUSED,
    NEW_STALE,
    REVIEW_POST,
    STALE_REFUSAL,
)
from .waits import eventually, hidden, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)
APPLIED_LINE = re.compile(r"Applied at \d{1,2}:\d{2}\s?[AP]M\.$")
FOCUSED = "document.activeElement && document.activeElement.id"


def fill_new(page, origin, path=NEW):
    """New user with an address and Name typed in (the lookup answered)."""
    page.goto(origin + path)
    email = page.get_by_label("Email", exact=True)
    email.fill("new@example.org")
    with page.expect_request(lambda request: request.method == "POST"):
        email.press("Tab")
    eventually(
        page,
        "document.querySelector('[data-names-help]')"
        ".textContent.startsWith('Suggested')",
    )
    page.get_by_label("Name", exact=True).fill("Smith, Ann")


def review(page, name="Add the user"):
    """Choose New user's Add the user (or Edit's Save); wait for the dialog."""
    page.get_by_role("button", name=name, exact=True).click()
    dialog = page.locator("dialog[data-review-dialog]")
    visible(dialog)
    return dialog


def watch(page):
    """Fail on any script error, and mark the page so a reload shows."""
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.evaluate("window.notReloaded = true")
    return failures


@pytest.mark.parametrize("width", [320, 1280])
def test_the_dialog_opens_on_its_title_and_fits(
    page, component_origin, axe_source, width
):
    """Modal, labelled by its title, which has focus; it fits the window."""
    page.set_viewport_size({"width": width, "height": 640})
    fill_new(page, component_origin)
    failures = watch(page)
    dialog = review(page)
    assert page.evaluate(FOCUSED) == "user-review-title"
    visible(page.get_by_role("dialog", name="Add this user?"))
    # A confirmation (#1356): one sentence, which describes the dialog, and
    # none of the values the form behind it already shows.
    summary = dialog.locator("#user-review-summary")
    assert summary.inner_text().startswith("This adds the user")
    assert dialog.get_attribute("aria-describedby") == "user-review-summary"
    assert "new@example.org" not in dialog.inner_text()
    assert "Smith, Ann" not in dialog.inner_text()
    # The page behind is inert: the form cannot be edited.
    assert page.evaluate("document.querySelector('#user-review').matches(':modal')")
    box = dialog.bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= width
    assert box["y"] >= 0 and box["y"] + box["height"] <= 640
    for name in ("Apply", "Cancel"):
        button = dialog.get_by_role("button", name=name)
        visible(button)
        edge = button.bounding_box()
        assert edge["y"] + edge["height"] <= 640, name
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert axe_violations(page, axe_source) == []
    assert page.evaluate("window.notReloaded === true")
    assert not failures


def test_the_form_is_inert_and_busy_while_the_review_is_prepared(
    page, component_origin
):
    """Until the answer, nothing in the form can be edited."""
    fill_new(page, component_origin)
    held = []
    page.route("**" + REVIEW_POST, lambda route: held.append(route))
    page.get_by_role("button", name="Add the user").click()
    eventually(page, "document.getElementById('user-entry-form').inert")
    form = page.locator("#user-entry-form")
    assert form.get_attribute("aria-busy") == "true"
    button = form.get_by_role("button", name="Add the user")
    assert button.get_attribute("aria-disabled") == "true"
    assert "is-busy" in (button.get_attribute("class") or "")
    eventually(page, "true")
    assert len(held) == 1
    held[0].continue_()
    visible(page.locator("dialog[data-review-dialog]"))
    assert not page.evaluate("document.getElementById('user-entry-form').inert")
    assert form.get_attribute("aria-busy") is None


def test_escape_and_cancel_close_it_and_return_to_review(page, component_origin):
    """Before Apply, the dialog closes, nothing changes, focus is on Add the user."""
    fill_new(page, component_origin)
    failures = watch(page)
    dialog = review(page)
    page.keyboard.press("Escape")
    hidden(dialog)
    eventually(page, "document.activeElement.textContent.trim() === 'Add the user'")
    assert page.get_by_label("Name", exact=True).input_value() == "Smith, Ann"
    dialog = review(page)
    dialog.get_by_role("button", name="Cancel").click()
    hidden(dialog)
    # The dialog's close event, which returns focus, follows its closing.
    eventually(page, "document.activeElement.textContent.trim() === 'Add the user'")
    assert page.evaluate("window.notReloaded === true")
    assert not failures


def test_apply_returns_to_users_with_the_status_line(page, component_origin):
    """Applied: the dialog closes, Users says so in its reserved line."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + LIST)
    table = page.locator("#users-table-title").bounding_box()["y"]
    fill_new(page, component_origin)
    dialog = review(page)
    with page.expect_request(lambda request: request.method == "POST") as sent:
        dialog.get_by_role("button", name="Apply").click()
    fields = parse_qs(sent.value.post_data or "")
    assert fields["action"] == ["confirm"]
    assert fields["preview"] == ["synthetic-preview"]
    assert set(fields) == {"csrfmiddlewaretoken", "action", "preview"}
    page.wait_for_url(re.compile(re.escape(LIST) + "$"))
    line = page.locator("#users-outcome")
    eventually(page, "document.getElementById('users-outcome').textContent !== ''")
    assert line.inner_text().startswith("User added. ")
    assert APPLIED_LINE.search(line.inner_text())
    assert line.get_attribute("role") == "status"
    assert page.evaluate(FOCUSED) == "users-outcome"
    # The line's space was there all along: the table did not move.
    assert page.locator("#users-table-title").bounding_box()["y"] == table
    # Shown once: a reload shows the empty line again.
    page.reload()
    assert page.locator("#users-outcome").inner_text() == ""


def test_once_applying_nothing_dismisses_the_dialog(page, component_origin):
    """No Escape, no Cancel until the answer; Apply goes once recorded."""
    fill_new(page, component_origin, NEW_PENDING)
    dialog = review(page)
    apply = dialog.get_by_role("button", name="Apply")
    actions = dialog.locator("[data-confirm-cancel]").bounding_box()
    apply.click()
    eventually(
        page,
        "document.querySelector('#user-review [data-confirm-accept]')"
        ".hasAttribute('data-recorded')",
    )
    assert dialog.locator("[data-confirm-status]").inner_text() == "Applying…"
    # Apply is gone from view and from the accessibility tree; its space
    # stays, so Cancel did not move.
    assert dialog.get_by_role("button", name="Apply").count() == 0
    assert dialog.locator("[data-confirm-cancel]").bounding_box() == actions
    assert dialog.locator("[data-confirm-cancel]").is_disabled()
    page.keyboard.press("Escape")
    page.keyboard.press("Escape")
    eventually(page, "document.getElementById('user-review').open")
    assert page.evaluate("document.getElementById('user-review').open")
    assert dialog.is_visible()


def test_a_field_error_stays_on_the_form(page, component_origin):
    """The dialog stays shut; the Name is marked; the summary has focus."""
    fill_new(page, component_origin, NEW_INVALID)
    failures = watch(page)
    page.get_by_role("button", name="Add the user").click()
    summary = page.locator("[data-error-summary]")
    visible(summary)
    eventually(page, "document.activeElement?.matches('[data-error-summary]')")
    assert not page.locator("dialog[data-review-dialog]").is_visible()
    name = page.get_by_label("Name", exact=True)
    assert name.get_attribute("aria-invalid") == "true"
    visible(page.locator("#id_name_error"))
    assert page.locator("#id_name_error").inner_text() == "Enter a name."
    # The reader's values stay, and the form can be edited again.
    assert name.input_value() == "Smith, Ann"
    assert page.get_by_label("Email", exact=True).input_value() == "new@example.org"
    assert not page.evaluate("document.getElementById('user-entry-form').inert")
    # The summary links to the field; an edit clears the mark.
    summary.get_by_role("link", name="Enter a name.").click()
    assert page.evaluate(FOCUSED) == "id_name"
    name.fill("Smith, Ann B")
    assert name.get_attribute("aria-invalid") == "false"
    assert page.evaluate("window.notReloaded === true")
    assert not failures


def test_another_refusal_shows_in_the_dialog(page, component_origin):
    """A stale page: the server's words below the buttons, and no Apply."""
    fill_new(page, component_origin, NEW_STALE)
    dialog = review(page)
    error = dialog.locator("[data-confirm-error]")
    visible(error)
    assert STALE_REFUSAL in error.inner_text()
    assert dialog.get_by_role("button", name="Apply").count() == 0
    assert page.evaluate(FOCUSED) == "user-review-title"
    dialog.get_by_role("button", name="Cancel").click()
    hidden(dialog)
    assert page.get_by_label("Name", exact=True).input_value() == "Smith, Ann"


def test_edit_review_waits_for_a_change_and_greys_again_on_revert(
    page, component_origin
):
    """No review of nothing: Save is unavailable until a real change."""
    from playwright.sync_api import expect

    page.goto(component_origin + EDIT)
    button = page.get_by_role("button", name="Save", exact=True)
    assert button.is_disabled()
    roles = page.get_by_role("group", name="Role")
    roles.get_by_label("Staff").check()
    assert button.is_enabled()
    roles.get_by_label("Ministry leader").check()
    assert button.is_disabled()
    visible(page.get_by_text("Change the Name or role to save it."))
    roles.get_by_label("Staff").check()
    failures = watch(page)
    dialog = review(page, "Save")
    visible(page.get_by_role("dialog", name="Save this user change?"))
    assert page.evaluate(FOCUSED) == "user-review-title"
    # A compact review (#1356, class A2): one line per changed field, which
    # describes the dialog; no Now and After table, and no email heading.
    assert dialog.get_attribute("aria-describedby") == "user-review-summary"
    lines = dialog.locator("#user-review-summary li")
    assert lines.count() == 1
    text = lines.first.inner_text()
    assert text.startswith("Role: Ministry leader") and text.endswith("Staff")
    assert "→" in text and dialog.locator("table").count() == 0
    # Screen readers hear "old, becomes new"; the arrow is hidden from them.
    expect(dialog).to_have_accessible_description(
        re.compile(r"^Role: Ministry leader ?, becomes Staff$")
    )
    dialog.get_by_role("button", name="Cancel").click()
    hidden(dialog)
    eventually(page, "document.activeElement.textContent.trim() === 'Save'")
    assert not failures


def test_the_import_applies_and_redraws_users_in_place(page, component_origin):
    """Applied: the dialog closes, the tables are redrawn, the line says so."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + LIST)
    failures = watch(page)
    page.get_by_role("button", name="Import all").click()
    dialog = page.get_by_role("dialog", name="Review Ministry leader import")
    visible(dialog)
    assert page.evaluate(FOCUSED) == "leader-review-title"
    with page.expect_request(lambda request: request.method == "POST") as sent:
        dialog.get_by_role("button", name="Apply").click()
    # Every hidden field of the review is posted, not only its preview.
    fields = parse_qs(sent.value.post_data or "")
    assert fields["preview"] == ["synthetic-preview"] and fields["extra"] == ["kept"]
    hidden(page.locator("#leader-review"))
    eventually(page, "document.getElementById('leaders-outcome').textContent !== ''")
    # The line above the leaders table, where the reader acted; the users
    # table's own line stays empty.
    line = page.locator("#leaders-outcome")
    assert line.inner_text().startswith("Leaders imported. ")
    assert APPLIED_LINE.search(line.inner_text())
    assert page.evaluate(FOCUSED) == "leaders-outcome"
    assert page.locator("#users-outcome").inner_text() == ""
    # Redrawn in place from the refresh address (the Ministry leader gone).
    eventually(
        page, "document.querySelectorAll('table.users-table tbody tr').length === 3"
    )
    assert page.evaluate("window.notReloaded === true")
    assert not failures


def test_a_refused_apply_shows_in_the_dialog_which_can_then_close(
    page, component_origin
):
    """The request's own refusal, in its words, below the buttons; Cancel
    closes the dialog and the form still holds the reader's values."""
    fill_new(page, component_origin, NEW_REFUSED)
    failures = watch(page)
    dialog = review(page)
    actions = dialog.locator("[data-confirm-cancel]").bounding_box()
    dialog.get_by_role("button", name="Apply").click()
    error = dialog.locator("[data-confirm-error]")
    visible(error)
    assert error.inner_text() == APPLY_REFUSAL + " Nothing was changed."
    assert dialog.locator("[data-confirm-accept]").is_disabled()
    cancel = dialog.get_by_role("button", name="Cancel")
    assert cancel.is_enabled()
    assert cancel.bounding_box() == actions
    cancel.click()
    hidden(dialog)
    assert page.get_by_label("Name", exact=True).input_value() == "Smith, Ann"
    assert page.evaluate("window.notReloaded === true")
    assert not failures


def test_cancel_never_moves_while_apply_is_sent(page, component_origin):
    """While the Apply POST is held, Apply shows no spinner and Cancel stays
    exactly where it was; the status line shows the progress."""
    fill_new(page, component_origin)
    dialog = review(page)
    cancel = dialog.locator("[data-confirm-cancel]")
    before = cancel.bounding_box()
    held = []
    page.route("**" + APPLY, lambda route: held.append(route))
    dialog.get_by_role("button", name="Apply").click()
    eventually(
        page,
        "document.querySelector('#user-review [data-confirm-status]')"
        ".textContent === 'Applying…'",
    )
    eventually(page, "true")
    assert len(held) == 1
    assert cancel.bounding_box() == before
    assert cancel.is_disabled()
    accept = dialog.locator("[data-confirm-accept]")
    assert "is-busy" not in (accept.get_attribute("class") or "")
    assert accept.get_attribute("aria-disabled") == "true"
    held[0].continue_()
    page.wait_for_url(re.compile(re.escape(LIST) + "$"))


# A refusal as long as #1464's (message, fix and a second sentence), the
# kind a phone shows on several lines.
LONG_REFUSAL = {
    "errors": [{"code": "stale_version"}],
    "refusal": {
        "message": "A send time in this change has passed since it was "
        "reviewed, so that email would send at once.",
        "fix": "Nothing was changed. Choose a later time, then review the changes.",
    },
}
LONG_REFUSAL_TEXT = " ".join(LONG_REFUSAL["refusal"].values())


def long_review(page, origin, size, refusal=None):
    """New user's review made taller than a window of ``size`` (#1482),
    whose Apply is refused with the fixture's sentence or ``refusal``."""
    page.set_viewport_size({"width": size[0], "height": size[1]})
    if refusal:
        page.route(
            "**" + APPLY_REFUSED,
            lambda route: route.fulfill(
                status=409, content_type="application/json", body=json.dumps(refusal)
            ),
        )
    fill_new(page, origin, NEW_REFUSED)
    dialog = review(page)
    # A long review (a repeat's thirty reminders) stands in as a tall block,
    # sized through the CSSOM (the page's CSP refuses style attributes).
    page.evaluate(
        "document.querySelector('#user-review [data-review-body]')"
        ".appendChild(document.createElement('div')).style.height = '2000px'"
    )
    body = dialog.locator("[data-review-body]")
    assert body.evaluate("node => node.scrollHeight > node.clientHeight + 100")
    return dialog


def refuse(dialog, text):
    """Press Apply and wait for the refusal ``text`` below the buttons."""
    dialog.get_by_role("button", name="Apply").click()
    error = dialog.locator("[data-confirm-error]")
    visible(error)
    assert error.inner_text() == text
    return error


SIZES = [(320, 480), (640, 360), (1280, 480)]
REFUSALS = {
    "short": (None, APPLY_REFUSAL + " Nothing was changed."),
    "long": (LONG_REFUSAL, LONG_REFUSAL_TEXT),
}


@pytest.mark.parametrize("kind", REFUSALS)
@pytest.mark.parametrize("size", SIZES, ids=lambda size: "x".join(map(str, size)))
def test_a_refusal_moves_the_buttons_of_a_long_review_only_by_its_excess(
    page, component_origin, size, kind
):
    """A review taller than the window (#1482): its body scrolls, and a
    refused Apply shows below the buttons, whole and inside the window. A
    refusal that fits the room the review left (min(7rem, 20dvh)) does not
    move the buttons; a taller one squeezes the body and raises them by
    exactly what does not fit."""
    refusal, text = REFUSALS[kind]
    failures = watch(page)
    dialog = long_review(page, component_origin, size, refusal)
    body = dialog.locator("[data-review-body]")
    cancel = dialog.get_by_role("button", name="Cancel")
    before = cancel.bounding_box()
    height = body.bounding_box()["height"]
    error = refuse(dialog, text)
    # The refusal's outer height (its 1rem margins too) beyond the room.
    outer = error.evaluate(
        "node => node.getBoundingClientRect().height"
        " + parseFloat(getComputedStyle(node).marginTop)"
        " + parseFloat(getComputedStyle(node).marginBottom)"
    )
    excess = max(0, outer - min(7 * 16, size[1] * 0.2))
    if size == (1280, 480) and kind == "short":
        # The everyday case: a one-line refusal fits and nothing moves.
        assert excess == 0
    after = cancel.bounding_box()
    assert abs(after["x"] - before["x"]) < 1
    assert abs(before["y"] - after["y"] - excess) < 1
    assert abs(height - body.bounding_box()["height"] - excess) < 1
    # Never cut short or scrolled: the whole refusal shows, in the window.
    assert error.evaluate("node => node.scrollHeight <= node.clientHeight")
    edge = error.bounding_box()
    assert edge["y"] >= 0 and edge["y"] + edge["height"] <= size[1]
    box = dialog.bounding_box()
    assert box["y"] + box["height"] <= size[1]
    assert not failures


@pytest.mark.parametrize("kind", REFUSALS)
@pytest.mark.parametrize(
    "size", [(320, 480), (640, 360)], ids=lambda size: "x".join(map(str, size))
)
def test_a_long_review_with_a_refusal_passes_axe_on_a_phone(
    page, component_origin, axe_source, size, kind
):
    """With a refusal shown on a phone-sized window (#1482), nothing in the
    dialog is a scroller the keyboard cannot reach, and axe finds nothing."""
    refusal, text = REFUSALS[kind]
    dialog = long_review(page, component_origin, size, refusal)
    refuse(dialog, text)
    assert axe_violations(page, axe_source) == []
