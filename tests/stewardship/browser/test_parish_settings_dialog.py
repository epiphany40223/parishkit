"""Parish settings reviewed and applied in the shared review dialog (#750).

Save waits for a real change (#921); Save opens the dialog with a compact
review (#1356), one "Setting: old → new" line per changed setting that
describes the dialog, and a new timezone's note; once Apply is applied,
the dialog closes, the form's region is redrawn in place at the applied
version, and the status line under the form says what was applied, with
no reload and no jump to the top. A field's error stays on the form; any
other refusal shows in the dialog and never redraws the form. Pages and
POST answers are ``settings_components``' (``PARISH`` and the names after
it); a route answers each Review POST.
"""

import json
import re

import pytest

from .review_helpers import RESTORE, answer_reviews
from .review_helpers import expect_review as expect_gate
from .settings_components import PARISH, PARISH_REFUSED, PARISH_REVIEW
from .test_in_place import MARK, MARKED, count_requests
from .waits import eventually, has_attribute, hidden, same_place, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)
FOCUSED = "document.activeElement && document.activeElement.id"
# The form's Save (its Review, #1356) and the line saying why it waits.
SAVE = "Save"
SAVE_HINT = "Change a setting to save it."
APPLIED_LINE = re.compile(
    r"^Parish settings changed\. Applied at \d{1,2}:\d{2}\s?[AP]M\.$"
)
DIGEST = "#settings-form input[name=base_digest]"
# Where an element sits on the page (not in the window, which may scroll).
PLACE = """(id) => {
    const box = document.getElementById(id).getBoundingClientRect();
    return [box.left + window.scrollX, box.top + window.scrollY];
}"""


def expect_review(page, *, enabled):
    """Save is available (or unavailable, with its hint) as ``enabled``."""
    expect_gate(page, enabled=enabled, hint=SAVE_HINT, name=SAVE)


def watch(page):
    """Fail on any script error, and mark the page so a reload shows."""
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.evaluate(MARK)
    return failures


def dialog_of(page):
    """The review dialog, once it is open."""
    dialog = page.get_by_role("dialog", name="Save the Parish settings?")
    visible(dialog)
    return dialog


def refuse_with(page, origin, action, status, message, fix):
    """Answer ``action``'s POSTs (preview or confirm) with a closed refusal,
    as the server's error_response draws it; other requests fall back."""

    def refuse(route):
        """Refuse the matching POST; let everything else through."""
        if f"action={action}" not in (route.request.post_data or ""):
            route.fallback()
            return
        route.fulfill(
            status=status,
            content_type="application/json",
            body=json.dumps(
                {
                    "errors": [{"code": "stale"}],
                    "refusal": {"message": message, "fix": fix},
                }
            ),
        )

    page.route(lambda url: url.split("#")[0] == origin + PARISH, refuse)


def test_a_change_is_reviewed_and_applied_in_the_dialog(page, component_origin):
    """Save opens the dialog on its title with a compact review of each
    change, which describes the dialog, and the timezone note; Apply closes
    it, redraws the form in place at the applied version, says what was
    applied in the status line and leaves Save waiting for the next change.
    Nothing reloads, the form and the status line do not move, and Apply is
    posted once."""
    from playwright.sync_api import expect

    reviews = answer_reviews(page, component_origin, PARISH_REVIEW, address=PARISH)
    page.goto(component_origin + PARISH)
    failures = watch(page)
    posts = count_requests(page, "POST", PARISH)
    expect_review(page, enabled=False)
    page.get_by_label("Parish name").fill("Renamed Parish")
    page.get_by_label("Parish timezone").select_option("America/Chicago")
    expect_review(page, enabled=True)
    page.get_by_role("button", name=SAVE, exact=True).click()
    dialog = dialog_of(page)
    assert page.evaluate(FOCUSED) == "parish-review-title"
    # A compact review (#1356): one line per changed setting, describing
    # the dialog, with no heading or table of its own.
    lines = dialog.locator("#parish-review-summary li")
    assert lines.count() == 2
    text = lines.nth(0).inner_text()
    assert text.startswith("Parish name: Sample Parish") and "→" in text
    assert text.endswith(" Renamed Parish")
    assert dialog.get_by_role("heading").count() == 1
    assert dialog.locator("table").count() == 0
    assert dialog.get_attribute("aria-describedby") == "parish-review-summary"
    expect(dialog).to_have_accessible_description(
        re.compile(r"^Parish name: Sample Parish ?, becomes Renamed Parish")
    )
    assert "This timezone change is prospective." in dialog.inner_text()
    assert "Renamed Parish" in dialog.inner_text()
    assert len(reviews) == 1 and "name=Renamed+Parish" in reviews[0]
    places = [page.evaluate(PLACE, id) for id in ("settings-form", "parish-outcome")]
    dialog.get_by_role("button", name="Apply").click()
    hidden(dialog)
    eventually(page, "document.getElementById('parish-outcome').textContent !== ''")
    line = page.locator("#parish-outcome")
    assert APPLIED_LINE.search(line.inner_text()), line.inner_text()
    assert page.evaluate(FOCUSED) == "parish-outcome"
    has_attribute(page.locator(DIGEST), "value", "b" * 64)
    assert page.get_by_label("Parish name").input_value() == "Renamed Parish"
    assert page.locator("[data-edits-lost]").count() == 0
    assert same_place(
        [page.evaluate(PLACE, id) for id in ("settings-form", "parish-outcome")],
        places,
    )
    # The redrawn values are the saved ones, so nothing is left to review.
    expect_review(page, enabled=False)
    assert page.url == component_origin + PARISH
    assert page.evaluate(MARKED) == "kept"
    assert len(posts) == 2
    assert not failures


def test_review_waits_for_a_change(page, component_origin):
    """Save is unavailable until the form differs from the saved
    settings, available after an edit, unavailable again once the edit is
    undone, and checked again when a page restored from history is shown
    with values that came back without events (#921). Its hint keeps its
    space, so the form and the status line never move."""
    reviews = answer_reviews(page, component_origin, PARISH_REVIEW, address=PARISH)
    page.goto(component_origin + PARISH)
    expect_review(page, enabled=False)
    below = "document.getElementById('parish-outcome').offsetTop"
    before = page.evaluate(below)
    name = page.get_by_label("Parish name")
    # Pressing Enter in a field cannot submit an unchanged form either.
    name.press("Enter")
    name.fill("Renamed Parish")
    expect_review(page, enabled=True)
    assert page.evaluate(below) == before
    name.fill("Sample Parish")
    expect_review(page, enabled=False)
    # Spaces the server trims are no change; a choice is.
    name.fill("Sample Parish ")
    expect_review(page, enabled=False)
    page.get_by_label("Parish timezone").select_option("America/Chicago")
    expect_review(page, enabled=True)
    page.get_by_label("Parish timezone").select_option("America/New_York")
    expect_review(page, enabled=False)
    # A restore without events, then pageshow: the gate follows the values.
    name.evaluate("field => { field.value = 'Restored name'; }")
    page.evaluate(RESTORE)
    expect_review(page, enabled=True)
    name.evaluate("field => { field.value = 'Sample Parish'; }")
    page.evaluate(RESTORE)
    expect_review(page, enabled=False)
    assert page.evaluate(below) == before
    assert reviews == []


def test_a_field_error_is_marked_and_keeps_the_dialog_shut(page, component_origin):
    """A Review refused for a field's value is shown on the form: the field
    is marked with its message, the summary above the heading links to it,
    the reader's typing stays, and no dialog opens."""
    answer_reviews(page, component_origin, PARISH_REFUSED, 400, address=PARISH)
    page.goto(component_origin + PARISH)
    failures = watch(page)
    phone = page.get_by_label("Parish telephone")
    phone.fill("123")
    page.get_by_role("button", name=SAVE, exact=True).click()
    summary = page.locator("[data-error-summary]")
    visible(summary)
    has_attribute(phone, "aria-invalid", "true")
    visible(page.locator("#id_phone_error"))
    assert not page.locator("dialog[data-review-dialog]").is_visible()
    summary.get_by_role("link", name="Parish telephone: Enter a ten-digit").click()
    assert page.evaluate(FOCUSED) == "id_phone"
    assert phone.input_value() == "123"
    assert page.evaluate(MARKED) == "kept"
    assert not failures


def test_a_stale_page_is_refused_in_the_dialog(page, component_origin):
    """A Review refused because the settings changed elsewhere shows the
    server's explanation in the dialog, which says to reload, with no Apply;
    the form keeps the reader's values at the version they came from."""
    refuse_with(
        page,
        component_origin,
        "preview",
        409,
        "This page changed in another tab or session.",
        "Reload the page to see the latest version, then make your change again.",
    )
    page.goto(component_origin + PARISH)
    page.get_by_label("Parish name").fill("Renamed Parish")
    page.get_by_role("button", name=SAVE, exact=True).click()
    dialog = dialog_of(page)
    error = dialog.locator("[data-confirm-error]")
    visible(error)
    assert "changed in another tab" in error.inner_text()
    assert "Reload the page" in error.inner_text()
    assert dialog.get_by_role("button", name="Apply").count() == 0
    dialog.get_by_role("button", name="Cancel").click()
    hidden(dialog)
    assert page.get_by_label("Parish name").input_value() == "Renamed Parish"
    assert page.locator(DIGEST).input_value() == "a" * 64


def test_a_refused_apply_shows_in_the_dialog_without_a_redraw(page, component_origin):
    """An Apply refused because the reviewed preview is out of date says
    why in the dialog, below its buttons, and stays until closed; the page
    behind is not redrawn (no reload, no status line) and keeps the
    reader's values at their version."""
    answer_reviews(page, component_origin, PARISH_REVIEW, address=PARISH)
    refuse_with(
        page,
        component_origin,
        "confirm",
        409,
        "This preview is out of date.",
        "Review the changes again, then confirm.",
    )
    page.goto(component_origin + PARISH)
    failures = watch(page)
    page.get_by_label("Parish name").fill("Renamed Parish")
    page.get_by_role("button", name=SAVE, exact=True).click()
    dialog = dialog_of(page)
    form = page.locator("#settings-form").element_handle()
    dialog.get_by_role("button", name="Apply").click()
    error = dialog.locator("[data-confirm-error]")
    visible(error)
    assert "This preview is out of date." in error.inner_text()
    dialog.get_by_role("button", name="Cancel").click()
    hidden(dialog)
    assert page.locator("#parish-outcome").inner_text() == ""
    # The very form element the reader used is still there: no redraw.
    assert form.evaluate("node => node.isConnected")
    assert page.get_by_label("Parish name").input_value() == "Renamed Parish"
    assert page.locator(DIGEST).input_value() == "a" * 64
    assert page.evaluate(MARKED) == "kept"
    assert not failures
