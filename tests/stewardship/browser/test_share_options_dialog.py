"""Share options reviewed and applied in the shared review dialog (#750).

Review changes waits for a real change to the list (#921); Review opens the
dialog with the current and proposed order and labels; once Apply is
applied, the dialog closes, the form's region is redrawn in place with the
applied list and a fresh blank row, and the status line under the form says
what was applied, with no reload and no jump to the top. A row's error stays
on the form; any other refusal shows in the dialog. Pages and POST answers
are ``settings_components``' (``SHARE`` and the names after it); a route
answers each Review POST.
"""

import json
import re

import pytest

from .settings_components import SHARE, SHARE_REFUSED, SHARE_REVIEW
from .test_in_place import MARK, MARKED
from .test_settings_in_place import RESTORE, answer_reviews, expect_review
from .waits import eventually, has_attribute, hidden, same_place, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)
FOCUSED = "document.activeElement && document.activeElement.id"
OPTION_HINT = "Change an option to review it."
APPLIED_LINE = re.compile(
    r"^Share options changed\. Applied at \d{1,2}:\d{2}\s?[AP]M\.$"
)
DIGEST = "#settings-form input[name=base_digest]"
# Where the form sits on the page (not in the window, which may scroll).
FORM_PLACE = """() => {
    const box = document.getElementById('settings-form').getBoundingClientRect();
    return [box.left + window.scrollX, box.top + window.scrollY];
}"""


def watch(page):
    """Fail on any script error, and mark the page so a reload shows."""
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.evaluate(MARK)
    return failures


def dialog_of(page):
    """The review dialog, once it is open."""
    dialog = page.get_by_role("dialog", name="Review Share options")
    visible(dialog)
    return dialog


def test_a_change_is_reviewed_and_applied_in_the_dialog(page, component_origin):
    """Review opens the dialog on its title with the current and proposed
    lists; Apply closes it, redraws the form in place with the applied list
    at the applied version, says what was applied in the status line and
    leaves Review waiting for the next change. Nothing reloads, the window
    does not jump to the top and the form does not move."""
    reviews = answer_reviews(page, component_origin, SHARE_REVIEW, address=SHARE)
    page.goto(component_origin + SHARE)
    failures = watch(page)
    expect_review(page, enabled=False, hint=OPTION_HINT)
    label = page.locator("#id_options-0-label")
    label.fill("Renamed option")
    expect_review(page, enabled=True, hint=OPTION_HINT)
    page.get_by_role("button", name="Review changes").click()
    dialog = dialog_of(page)
    assert page.evaluate(FOCUSED) == "share-review-title"
    visible(dialog.get_by_role("heading", name="Current order and labels"))
    visible(dialog.get_by_role("heading", name="Proposed order and labels"))
    assert "Renamed option" in dialog.inner_text()
    assert len(reviews) == 1 and "options-0-label=Renamed+option" in reviews[0]
    place = page.evaluate(FORM_PLACE)
    dialog.get_by_role("button", name="Apply").click()
    hidden(dialog)
    eventually(page, "document.getElementById('share-outcome').textContent !== ''")
    line = page.locator("#share-outcome")
    assert APPLIED_LINE.search(line.inner_text()), line.inner_text()
    assert page.evaluate(FOCUSED) == "share-outcome"
    has_attribute(page.locator(DIGEST), "value", "b" * 64)
    assert label.input_value() == "Renamed option"
    assert same_place(page.evaluate(FORM_PLACE), place)
    # The applied list is now the saved one.
    expect_review(page, enabled=False, hint=OPTION_HINT)
    assert page.evaluate(MARKED) == "kept"
    assert not failures


def test_review_waits_for_a_change(page, component_origin):
    """A renamed label, a deletion or a new row in the blank last row makes
    Review available, undoing each makes it unavailable again, and a restore
    is noticed on pageshow (#921). The status line under the form never
    moves."""
    page.goto(component_origin + SHARE)
    expect_review(page, enabled=False, hint=OPTION_HINT)
    below = "document.getElementById('share-outcome').offsetTop"
    before = page.evaluate(below)
    label = page.locator("#id_options-0-label")
    saved = label.input_value()
    label.fill("Renamed option")
    expect_review(page, enabled=True, hint=OPTION_HINT)
    label.fill(saved)
    expect_review(page, enabled=False, hint=OPTION_HINT)
    delete = page.locator("#id_options-0-DELETE")
    delete.check()
    expect_review(page, enabled=True, hint=OPTION_HINT)
    delete.uncheck()
    expect_review(page, enabled=False, hint=OPTION_HINT)
    total = int(page.locator("[name=options-TOTAL_FORMS]").input_value())
    blank = page.locator(f"#id_options-{total - 1}-label")
    assert blank.input_value() == ""
    blank.fill("A new option")
    expect_review(page, enabled=True, hint=OPTION_HINT)
    blank.fill("")
    expect_review(page, enabled=False, hint=OPTION_HINT)
    label.evaluate("field => { field.value = 'Restored label'; }")
    page.evaluate(RESTORE)
    expect_review(page, enabled=True, hint=OPTION_HINT)
    assert page.evaluate(below) == before


def test_a_refused_row_is_marked_and_keeps_the_dialog_shut(page, component_origin):
    """A Review refused for a row's value is shown on the form: the row's
    label is marked, the summary is shown, and no dialog opens."""
    answer_reviews(page, component_origin, SHARE_REFUSED, 400, address=SHARE)
    page.goto(component_origin + SHARE)
    failures = watch(page)
    page.locator("#id_options-0-label").fill("{{ family_code }}")
    page.get_by_role("button", name="Review changes").click()
    visible(page.locator("[data-error-summary]"))
    has_attribute(page.locator("#id_options-0-label"), "aria-invalid", "true")
    assert not page.locator("dialog[data-review-dialog]").is_visible()
    assert page.evaluate(MARKED) == "kept"
    assert not failures


def test_a_stale_page_is_refused_in_the_dialog(page, component_origin):
    """A Review refused because the list changed elsewhere shows the
    server's explanation in the dialog, with no Apply; the form keeps the
    reader's rows at the version they came from."""

    def stale(route):
        """Answer Review with the closed refusal of a stale page."""
        if route.request.method != "POST":
            route.continue_()
            return
        route.fulfill(
            status=409,
            content_type="application/json",
            body=json.dumps(
                {
                    "errors": [{"code": "stale"}],
                    "refusal": {
                        "message": "This page changed in another tab or session.",
                        "fix": "Reload the page to start from the current settings.",
                    },
                }
            ),
        )

    page.route(lambda url: url.split("#")[0] == component_origin + SHARE, stale)
    page.goto(component_origin + SHARE)
    page.locator("#id_options-0-label").fill("Renamed option")
    page.get_by_role("button", name="Review changes").click()
    dialog = dialog_of(page)
    error = dialog.locator("[data-confirm-error]")
    visible(error)
    assert "changed in another tab" in error.inner_text()
    assert dialog.get_by_role("button", name="Apply").count() == 0
    dialog.get_by_role("button", name="Cancel").click()
    hidden(dialog)
    assert page.locator("#id_options-0-label").input_value() == "Renamed option"
    assert page.locator(DIGEST).input_value() == "a" * 64


@pytest.mark.parametrize("edit", ["add", "delete"])
def test_review_is_unavailable_after_applying_a_row_change(
    page, component_origin, edit
):
    """Applying a new row or a deletion redraws Share options with the
    applied list and a fresh blank row; that redrawn list is the saved one,
    so Review changes is unavailable (#921)."""
    answer_reviews(page, component_origin, SHARE_REVIEW, address=SHARE)
    page.goto(component_origin + SHARE)
    if edit == "add":
        total = int(page.locator("[name=options-TOTAL_FORMS]").input_value())
        page.locator(f"#id_options-{total - 1}-label").fill("A new option")
    else:
        page.locator("#id_options-1-DELETE").check()
    page.get_by_role("button", name="Review changes").click()
    dialog = dialog_of(page)
    dialog.get_by_role("button", name="Apply").click()
    hidden(dialog)
    has_attribute(page.locator(DIGEST), "value", "b" * 64)
    expect_review(page, enabled=False, hint=OPTION_HINT)
