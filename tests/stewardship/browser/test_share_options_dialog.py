"""Share options reviewed and applied in the shared review dialog (#750).

Save waits for a real change to the list (#921); Save opens the dialog
with a compact review marking only what changed (#1356); once Apply is
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

from .review_helpers import RESTORE, answer_reviews, expect_review
from .settings_components import SHARE, SHARE_REFUSED, SHARE_REVIEW
from .test_in_place import MARK, MARKED
from .waits import eventually, has_attribute, hidden, same_place, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)
FOCUSED = "document.activeElement && document.activeElement.id"
OPTION_HINT = "Change an option to save it."
APPLIED_LINE = re.compile(
    r"^Share options changed\. Applied at \d{1,2}:\d{2}\s?[AP]M\.$"
)
DIGEST = "#settings-form input[name=base_digest]"
# Where an element sits on the page (not in the window, which may scroll).
PLACE = """(id) => {
    const box = document.getElementById(id).getBoundingClientRect();
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
    dialog = page.get_by_role("dialog", name="Save the share options?")
    visible(dialog)
    return dialog


def test_a_change_is_reviewed_and_applied_in_the_dialog(page, component_origin):
    """Save opens the dialog on its title with a compact review of the one
    change; Apply closes it, redraws the form in place with the applied list
    at the applied version, says what was applied in the status line and
    leaves Review waiting for the next change. Nothing reloads, the window
    does not jump to the top, the form and the status line do not move, and
    no line claims that typing was lost (what was typed was reviewed)."""
    from playwright.sync_api import expect

    reviews = answer_reviews(page, component_origin, SHARE_REVIEW, address=SHARE)
    page.goto(component_origin + SHARE)
    failures = watch(page)
    expect_review(page, enabled=False, hint=OPTION_HINT, name="Save")
    label = page.locator("#id_options-0-label")
    label.fill("Renamed option")
    expect_review(page, enabled=True, hint=OPTION_HINT, name="Save")
    page.get_by_role("button", name="Save", exact=True).click()
    dialog = dialog_of(page)
    assert page.evaluate(FOCUSED) == "share-review-title"
    # A compact review (#1356): one line for the one relabelled option,
    # which describes the dialog, never both whole lists.
    lines = dialog.locator("#share-review-summary li")
    assert lines.count() == 1
    text = lines.first.inner_text()
    assert text.startswith("Label: ") and text.endswith(" Renamed option")
    assert "→" in text
    assert dialog.get_by_role("heading").count() == 1
    assert dialog.locator("ol").count() == 0
    assert dialog.get_attribute("aria-describedby") == "share-review-summary"
    expect(dialog).to_have_accessible_description(
        re.compile(r"^Label: .+ ?, becomes Renamed option$")
    )
    assert len(reviews) == 1 and "options-0-label=Renamed+option" in reviews[0]
    places = [page.evaluate(PLACE, id) for id in ("settings-form", "share-outcome")]
    dialog.get_by_role("button", name="Apply").click()
    hidden(dialog)
    eventually(page, "document.getElementById('share-outcome').textContent !== ''")
    line = page.locator("#share-outcome")
    assert APPLIED_LINE.search(line.inner_text()), line.inner_text()
    assert page.evaluate(FOCUSED) == "share-outcome"
    has_attribute(page.locator(DIGEST), "value", "b" * 64)
    assert label.input_value() == "Renamed option"
    assert page.locator("[data-edits-lost]").count() == 0
    assert same_place(
        [page.evaluate(PLACE, id) for id in ("settings-form", "share-outcome")],
        places,
    )
    # The applied list is now the saved one.
    expect_review(page, enabled=False, hint=OPTION_HINT, name="Save")
    assert page.evaluate(MARKED) == "kept"
    assert not failures


def test_review_waits_for_a_change(page, component_origin):
    """A renamed label, a deletion or a new row in the blank last row makes
    Review available, undoing each makes it unavailable again, and a restore
    is noticed on pageshow (#921). The status line under the form never
    moves."""
    page.goto(component_origin + SHARE)
    expect_review(page, enabled=False, hint=OPTION_HINT, name="Save")
    below = "document.getElementById('share-outcome').offsetTop"
    before = page.evaluate(below)
    label = page.locator("#id_options-0-label")
    saved = label.input_value()
    label.fill("Renamed option")
    expect_review(page, enabled=True, hint=OPTION_HINT, name="Save")
    label.fill(saved)
    expect_review(page, enabled=False, hint=OPTION_HINT, name="Save")
    delete = page.locator("#id_options-0-DELETE")
    delete.check()
    expect_review(page, enabled=True, hint=OPTION_HINT, name="Save")
    delete.uncheck()
    expect_review(page, enabled=False, hint=OPTION_HINT, name="Save")
    total = int(page.locator("[name=options-TOTAL_FORMS]").input_value())
    blank = page.locator(f"#id_options-{total - 1}-label")
    assert blank.input_value() == ""
    blank.fill("A new option")
    expect_review(page, enabled=True, hint=OPTION_HINT, name="Save")
    blank.fill("")
    expect_review(page, enabled=False, hint=OPTION_HINT, name="Save")
    label.evaluate("field => { field.value = 'Restored label'; }")
    page.evaluate(RESTORE)
    expect_review(page, enabled=True, hint=OPTION_HINT, name="Save")
    assert page.evaluate(below) == before


def test_a_refused_row_is_marked_and_keeps_the_dialog_shut(page, component_origin):
    """A Review refused for a row's value is shown on the form: the row's
    label is marked, the summary is shown, and no dialog opens."""
    answer_reviews(page, component_origin, SHARE_REFUSED, 400, address=SHARE)
    page.goto(component_origin + SHARE)
    failures = watch(page)
    # A label the page admits, which this server answer refuses anyway (a
    # private placeholder is now held by the label rule before Save, #1577).
    page.locator("#id_options-0-label").fill("Edited label")
    page.get_by_role("button", name="Save", exact=True).click()
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
    page.get_by_role("button", name="Save", exact=True).click()
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
    so Save is unavailable (#921)."""
    answer_reviews(page, component_origin, SHARE_REVIEW, address=SHARE)
    page.goto(component_origin + SHARE)
    if edit == "add":
        total = int(page.locator("[name=options-TOTAL_FORMS]").input_value())
        page.locator(f"#id_options-{total - 1}-label").fill("A new option")
    else:
        page.locator("#id_options-1-DELETE").check()
    page.get_by_role("button", name="Save", exact=True).click()
    dialog = dialog_of(page)
    dialog.get_by_role("button", name="Apply").click()
    hidden(dialog)
    has_attribute(page.locator(DIGEST), "value", "b" * 64)
    expect_review(page, enabled=False, hint=OPTION_HINT, name="Save")


def test_a_refused_apply_shows_in_the_dialog(page, component_origin):
    """An Apply refused because the reviewed preview is out of date (the
    list changed elsewhere) says why in the dialog, below its buttons, and
    stays until closed; the page behind is not redrawn and keeps the
    reader's rows at their version."""
    answer_reviews(page, component_origin, SHARE_REVIEW, address=SHARE)

    def refuse(route):
        """Refuse Apply as the server does; Review falls back above."""
        if "action=confirm" not in (route.request.post_data or ""):
            route.fallback()
            return
        route.fulfill(
            status=409,
            content_type="application/json",
            body=json.dumps(
                {
                    "errors": [{"code": "stale"}],
                    "refusal": {
                        "message": "This preview is out of date.",
                        "fix": "Review the changes again, then confirm.",
                    },
                }
            ),
        )

    page.route(lambda url: url.split("#")[0] == component_origin + SHARE, refuse)
    page.goto(component_origin + SHARE)
    failures = watch(page)
    page.locator("#id_options-0-label").fill("Renamed option")
    page.get_by_role("button", name="Save", exact=True).click()
    dialog = dialog_of(page)
    dialog.get_by_role("button", name="Apply").click()
    error = dialog.locator("[data-confirm-error]")
    visible(error)
    assert "This preview is out of date." in error.inner_text()
    dialog.get_by_role("button", name="Cancel").click()
    hidden(dialog)
    assert page.locator("#share-outcome").inner_text() == ""
    assert page.locator("#id_options-0-label").input_value() == "Renamed option"
    assert page.locator(DIGEST).input_value() == "a" * 64
    assert page.evaluate(MARKED) == "kept"
    assert not failures
