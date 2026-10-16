"""The Reminder WorkGroup setting page (#861) in a real browser.

Save waits for a real change to the name (#921) and opens the shared review
dialog (#750) with a one-sentence confirmation (#1356) that repeats neither
name; once Apply is applied the dialog closes, the form and "Now" are
redrawn in place from the page's address and the status line under the form
says what was applied, with no reload and nothing moving. A name's error
stays on the form; any other refusal shows in the dialog. Pages and POST
answers are ``reminder_workgroup_components``'; a route answers each Review.
"""

import json
import re

import pytest
from playwright.sync_api import expect

from .reminder_workgroup_components import CONFIRM, EDITOR, NAME, REFUSED
from .review_helpers import answer_reviews
from .test_components import axe_violations
from .test_in_place import MARK, MARKED
from .waits import eventually, has_attribute, hidden, same_place, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)
STATES = (
    "/reminder-workgroup-found",
    "/reminder-workgroup-missing",
    "/reminder-workgroup-off",
)
FOCUSED = "document.activeElement && document.activeElement.id"
HINT = "Change the WorkGroup name to save it."
DIGEST = "#settings-form input[name=base_digest]"
TITLE = "Save the No-reminders WorkGroup?"
APPLIED_LINE = re.compile(
    r"^No-reminders WorkGroup changed\. Applied at \d{1,2}:\d{2}\s?[AP]M\.$"
)
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


def expect_save(page, *, enabled):
    """Save is available exactly when the name differs from the saved one
    (#921); otherwise its hint, which always keeps its space, says why."""
    button = page.get_by_role("button", name="Save")
    line = page.locator("#settings-form-unchanged-hint")
    expect(line).to_have_text(HINT)
    if enabled:
        expect(button).to_be_enabled()
        expect(line).to_have_css("visibility", "hidden")
    else:
        expect(button).to_be_disabled()
        expect(line).to_have_css("visibility", "visible")
        expect(button).to_have_accessible_description(HINT)


@pytest.mark.parametrize("width", [320, 1280])
def test_reminder_workgroup_pages_are_accessible_and_fit(
    page, component_origin, axe_source, width
):
    """Every state and the open confirmation pass axe and fit a phone
    without scrolling sideways."""
    page.set_viewport_size({"width": width, "height": 900})
    for path in STATES:
        failures = []
        page.on("pageerror", lambda error, seen=failures: seen.append(str(error)))
        page.goto(component_origin + path)
        assert not failures
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert axe_violations(page, axe_source) == [], path
    answer_reviews(page, component_origin, CONFIRM, address=EDITOR)
    page.goto(component_origin + EDITOR)
    page.get_by_label("ParishSoft Family WorkGroup").fill(NAME)
    page.get_by_role("button", name="Save").click()
    visible(page.get_by_role("dialog", name=TITLE))
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert axe_violations(page, axe_source) == []


def test_the_field_explains_itself_and_each_state_says_what_happens(
    page, component_origin
):
    """The name field has its plain-language help; each state is stated."""
    page.goto(component_origin + "/reminder-workgroup-found")
    field = page.get_by_label("ParishSoft Family WorkGroup")
    assert field.input_value() == "Active: Stewardship 2027"
    visible(page.get_by_text("Families in that WorkGroup get no Reminder emails"))
    visible(page.get_by_text("found 2 Families in"))
    page.goto(component_origin + "/reminder-workgroup-missing")
    visible(page.locator("[data-workgroup-status=missing]"))
    page.goto(component_origin + "/reminder-workgroup-off")
    assert page.get_by_label("ParishSoft Family WorkGroup").input_value() == ""
    visible(page.locator("[data-workgroup-status=off]"))


def test_save_confirms_in_the_dialog_and_applies_in_place(page, component_origin):
    """Save waits for a change; it opens the dialog on its title with one
    sentence, its description, and neither name; Apply closes it, redraws
    the form at the applied version and "Now" in place, and says what was
    applied in the status line. Nothing reloads, and the form and the status
    line do not move (#1356)."""
    reviews = answer_reviews(page, component_origin, CONFIRM, address=EDITOR)
    page.goto(component_origin + EDITOR)
    failures = watch(page)
    expect_save(page, enabled=False)
    field = page.get_by_label("ParishSoft Family WorkGroup")
    field.fill(NAME)
    expect_save(page, enabled=True)
    page.get_by_role("button", name="Save").click()
    dialog = page.get_by_role("dialog", name=TITLE)
    visible(dialog)
    assert page.evaluate(FOCUSED) == "workgroup-review-title"
    assert dialog.get_attribute("aria-describedby") == "workgroup-review-summary"
    summary = dialog.locator("#workgroup-review-summary")
    assert summary.inner_text().startswith("From the next ParishSoft refresh")
    assert NAME not in dialog.inner_text()
    assert len(reviews) == 1 and "name=Active" in reviews[0]
    places = [page.evaluate(PLACE, id) for id in ("settings-form", "workgroup-outcome")]
    dialog.get_by_role("button", name="Apply").click()
    hidden(dialog)
    eventually(page, "document.getElementById('workgroup-outcome').textContent !== ''")
    line = page.locator("#workgroup-outcome")
    assert APPLIED_LINE.search(line.inner_text()), line.inner_text()
    assert page.evaluate(FOCUSED) == "workgroup-outcome"
    has_attribute(page.locator(DIGEST), "value", "b" * 64)
    visible(page.locator("#workgroup-status [data-workgroup-status=pending]"))
    assert field.input_value() == NAME
    assert same_place(
        [page.evaluate(PLACE, id) for id in ("settings-form", "workgroup-outcome")],
        places,
    )
    # The applied name is now the saved one.
    expect_save(page, enabled=False)
    assert page.evaluate(MARKED) == "kept"
    assert not failures


def test_cancel_changes_nothing_and_returns_to_save(page, component_origin):
    """Escape or Cancel before Apply closes the dialog; the typed name stays
    and focus is back on Save."""
    answer_reviews(page, component_origin, CONFIRM, address=EDITOR)
    page.goto(component_origin + EDITOR)
    field = page.get_by_label("ParishSoft Family WorkGroup")
    field.fill(NAME)
    page.get_by_role("button", name="Save").click()
    dialog = page.get_by_role("dialog", name=TITLE)
    visible(dialog)
    dialog.get_by_role("button", name="Cancel").click()
    hidden(dialog)
    eventually(page, "document.activeElement.textContent.trim() === 'Save'")
    assert field.input_value() == NAME
    assert page.locator(DIGEST).input_value() == "a" * 64


def test_a_refused_name_is_marked_and_keeps_the_dialog_shut(page, component_origin):
    """A Review refused for the name is shown on the form: the field is
    marked, the summary is shown, and no dialog opens."""
    answer_reviews(page, component_origin, REFUSED, 400, address=EDITOR)
    page.goto(component_origin + EDITOR)
    failures = watch(page)
    page.get_by_label("ParishSoft Family WorkGroup").fill("x" * 300)
    page.get_by_role("button", name="Save").click()
    visible(page.locator("[data-error-summary]"))
    has_attribute(page.locator("#id_name"), "aria-invalid", "true")
    assert not page.locator("dialog[data-review-dialog]").is_visible()
    assert page.evaluate(MARKED) == "kept"
    assert not failures


def test_a_stale_page_is_refused_in_the_dialog(page, component_origin):
    """A Review refused because the setting changed elsewhere shows the
    server's explanation in the dialog, with no Apply; the form keeps the
    reader's name at the version it came from."""

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

    page.route(lambda url: url.split("#")[0] == component_origin + EDITOR, stale)
    page.goto(component_origin + EDITOR)
    field = page.get_by_label("ParishSoft Family WorkGroup")
    field.fill(NAME)
    page.get_by_role("button", name="Save").click()
    dialog = page.locator("dialog[data-review-dialog]")
    visible(dialog)
    error = dialog.locator("[data-confirm-error]")
    visible(error)
    assert "changed in another tab" in error.inner_text()
    assert dialog.get_by_role("button", name="Apply").count() == 0
    dialog.get_by_role("button", name="Cancel").click()
    hidden(dialog)
    assert field.input_value() == NAME
    assert page.locator(DIGEST).input_value() == "a" * 64


FOLLOWS = (
    "([first, second]) => Boolean(document.querySelector(first)"
    ".compareDocumentPosition(document.querySelector(second))"
    " & Node.DOCUMENT_POSITION_FOLLOWING)"
)


@pytest.mark.parametrize("state", ["found", "missing", "off"])
def test_the_refresh_status_comes_after_the_form_and_its_status_line(
    page, component_origin, state
):
    """What the newest refresh found sits below the form and its status
    line, so a status whose length changes (the redraw after Apply) can
    never move the form above it (#736)."""
    page.goto(component_origin + f"/reminder-workgroup-{state}")
    assert page.evaluate(FOLLOWS, ["#settings-form", "#workgroup-outcome"])
    assert page.evaluate(FOLLOWS, ["#workgroup-outcome", "#workgroup-status"])
