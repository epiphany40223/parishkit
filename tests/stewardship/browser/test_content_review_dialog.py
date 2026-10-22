"""Pages and emails' editor reviews in the review dialog (#750, Slice C).

Preview changes posts the editor without a page load and opens the shared
review dialog with the full review (the fictional sample and the schedules
the new text switches); Apply returns to Pages and emails, whose status
line says what was applied and when. A refusal of the values stays on the
form, with an HTML source error shown at the visual editor too; any other
refusal shows in the dialog. Ticking "Remove this selected content" greys
the content in place (#563 item 6). Pages and answers are
``content_review_components``'.
"""

import re

import pytest

from .content_catalog_components import CATALOG
from .content_review_components import EDITOR, REFUSED, REVIEW, UNCHANGED
from .review_helpers import answer_reviews
from .test_components import axe_violations
from .test_in_place import MARK, MARKED
from .waits import available, eventually, greyed, has_attribute, hidden, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)
FOCUSED = "document.activeElement && document.activeElement.id"
TITLE = "Review changes to Reminder"
# Where an element sits on the page (not in the window, which may scroll).
PLACE = """(selector) => {
    const box = document.querySelector(selector).getBoundingClientRect();
    return [box.left + window.scrollX, box.top + window.scrollY, box.width, box.height];
}"""


def watch(page):
    """Fail on any script error, and mark the page so a reload shows."""
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.evaluate(MARK)
    return failures


def edit(page):
    """Change the email's text in the visual editor, as a reader types."""
    editor = page.locator("[data-content-editor]")
    editor.click()
    page.keyboard.press("End")
    page.keyboard.type(" Thank you.")


def test_preview_changes_reviews_in_the_dialog_and_returns_to_the_list(
    page, component_origin, axe_source
):
    """Preview changes opens the full review on its title, with the
    schedules by name (the sent one listed apart), their times in this
    browser's zone and only the signed preview to
    post; Apply returns to Pages and emails, whose status line says what was
    applied and when and takes focus."""
    reviews = answer_reviews(page, component_origin, REVIEW, address=EDITOR)
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + EDITOR)
    failures = watch(page)
    preview = page.get_by_role("button", name="Preview changes")
    assert preview.is_disabled()
    edit(page)
    available(preview)
    preview.click()
    dialog = page.get_by_role("dialog", name=TITLE)
    visible(dialog)
    assert page.evaluate(FOCUSED) == "content-review-title"
    for heading in ("Current content", "Proposed content"):
        visible(dialog.get_by_role("heading", name=heading))
    assert "Please respond by Monday." in dialog.inner_text()
    visible(dialog.get_by_text("Schedules using this template"))
    # Named as Dates and mail schedules names them, the sent one apart.
    visible(dialog.get_by_role("heading", name="Already sent"))
    lists = dialog.locator("#content-review-schedules ~ ul")
    assert lists.nth(0).inner_text().startswith("Reminder 2 — ")
    assert lists.nth(1).inner_text().startswith("Reminder 1 — ")
    shown = dialog.locator("time[data-local-instant]").first.inner_text()
    assert "2054" in shown and "+00:00" not in shown and "T13" not in shown
    # Each send in the shared schedule preview's words: its weekday, date
    # and browser-local time, with no "at" and no zone name (#1949).
    assert re.fullmatch(
        r"[A-Z][a-z]+day, [A-Z][a-z]+ \d{1,2}, 2054, \d{1,2}:\d{2} [AP]M", shown
    ), shown
    carried = dialog.locator("form[data-review-apply] input[data-review-carried]")
    assert carried.evaluate_all("nodes => nodes.map((node) => node.name)") == [
        "preview"
    ]
    assert len(reviews) == 1 and "action=preview" in reviews[0]
    assert axe_violations(page, axe_source) == []
    assert page.evaluate(MARKED) == "kept"
    assert not failures
    with page.expect_navigation():
        dialog.get_by_role("button", name="Apply").click()
    assert page.url == component_origin + CATALOG
    line = page.locator("#content-outcome")
    eventually(page, "document.getElementById('content-outcome').textContent !== ''")
    assert re.search(
        r"^Reminder changed\. Applied at \d{1,2}:\d{2}\s?[AP]M\.$", line.inner_text()
    ), line.inner_text()
    assert page.evaluate(FOCUSED) == "content-outcome"


def test_cancel_changes_nothing_and_returns_focus(page, component_origin):
    """Cancel closes the review, posts nothing more, and gives
    focus back to Preview changes; the reader's edit stays."""
    reviews = answer_reviews(page, component_origin, REVIEW, address=EDITOR)
    page.goto(component_origin + EDITOR)
    failures = watch(page)
    edit(page)
    page.get_by_role("button", name="Preview changes").click()
    dialog = page.get_by_role("dialog", name=TITLE)
    visible(dialog)
    dialog.get_by_role("button", name="Cancel").click()
    hidden(dialog)
    eventually(page, "document.activeElement.textContent.trim()", "Preview changes")
    assert "Thank you." in page.locator("[data-content-editor]").inner_text()
    assert len(reviews) == 1
    assert page.evaluate(MARKED) == "kept"
    assert not failures


def test_a_refused_value_stays_on_the_form_and_at_the_visual_editor(
    page, component_origin
):
    """A Preview refused for the HTML keeps the dialog shut: the summary is
    placed above the heading and the error is shown at the visual editor,
    which is marked; the next edit there clears it. Nothing reloads."""
    answer_reviews(page, component_origin, REFUSED, 400, address=EDITOR)
    page.goto(component_origin + EDITOR)
    failures = watch(page)
    edit(page)
    page.get_by_role("button", name="Preview changes").click()
    visible(page.locator("[data-error-summary]"))
    editor = page.locator("[data-content-editor]")
    message = page.locator("[data-visual-content] #visual-content-error")
    visible(message)
    assert "placeholder" in message.inner_text().lower()
    has_attribute(editor, "aria-invalid", "true")
    has_attribute(editor, "aria-describedby", "visual-content-error")
    assert not page.locator("dialog#content-review").is_visible()
    assert "Thank you." in editor.inner_text()
    source = page.locator('textarea[name="html"]')
    has_attribute(source, "aria-invalid", "true")
    editor.click()
    page.keyboard.type("!")
    hidden(message)
    assert editor.get_attribute("aria-invalid") is None
    # The HTML source's own mark and summary item go with it (sync() writes
    # the source's value, which fires no input event of its own).
    assert source.get_attribute("aria-invalid") != "true"
    assert page.locator("[data-error-summary]").count() == 0
    assert page.evaluate(MARKED) == "kept"
    assert not failures


def test_fixing_the_html_source_clears_the_visual_editors_error(page, component_origin):
    """A refused HTML error fixed in the HTML source instead of the visual
    editor clears the visual editor's copy and mark too."""
    answer_reviews(page, component_origin, REFUSED, 400, address=EDITOR)
    page.goto(component_origin + EDITOR)
    failures = watch(page)
    edit(page)
    page.get_by_role("button", name="Preview changes").click()
    message = page.locator("[data-visual-content] #visual-content-error")
    visible(message)
    page.get_by_text("HTML source", exact=True).click()
    page.locator('textarea[name="html"]').click()
    page.keyboard.press("End")
    page.keyboard.type(" ")
    hidden(message)
    editor = page.locator("[data-content-editor]")
    assert editor.get_attribute("aria-invalid") is None
    assert editor.get_attribute("aria-describedby") is None
    assert page.evaluate(MARKED) == "kept"
    assert not failures


def test_any_other_refusal_shows_in_the_dialog(page, component_origin):
    """A refusal that is not about a value (here the server's safeguard for a
    Preview that changes nothing) is shown in the dialog with its fix and no
    Apply; Cancel closes it and the form keeps the reader's version."""
    answer_reviews(page, component_origin, UNCHANGED, 400, address=EDITOR)
    page.goto(component_origin + EDITOR)
    failures = watch(page)
    edit(page)
    page.get_by_role("button", name="Preview changes").click()
    dialog = page.get_by_role("dialog", name=TITLE)
    visible(dialog)
    error = dialog.locator("[data-confirm-error]")
    visible(error)
    assert "No content has changed. Nothing was changed." in error.inner_text()
    assert dialog.get_by_role("button", name="Apply").is_hidden()
    dialog.get_by_role("button", name="Cancel").click()
    hidden(dialog)
    assert page.locator("input[name=base_digest]").input_value() == "a" * 64
    assert page.evaluate(MARKED) == "kept"
    assert not failures


def test_a_long_review_scrolls_inside_the_dialog_on_a_phone(page, component_origin):
    """At phone width the dialog fits the window: its body scrolls while the
    title and the Apply and Cancel buttons stay in view."""
    answer_reviews(page, component_origin, REVIEW, address=EDITOR)
    page.set_viewport_size({"width": 375, "height": 640})
    page.goto(component_origin + EDITOR)
    edit(page)
    page.get_by_role("button", name="Preview changes").click()
    dialog = page.get_by_role("dialog", name=TITLE)
    visible(dialog)
    box = dialog.bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= 375 + 1
    body = dialog.locator("[data-review-body]")
    assert body.evaluate("node => node.scrollHeight > node.clientHeight")
    for name in ("Apply", "Cancel"):
        button = dialog.get_by_role("button", name=name).bounding_box()
        assert button["y"] >= 0 and button["y"] + button["height"] <= 640 + 1
    assert page.evaluate("document.documentElement.scrollWidth") <= 375 + 1


def test_remove_greys_the_content_in_place(page, component_origin):
    """Ticking "Remove this selected content" greys the subject, HTML
    source, generated-text box and plain text, and makes the visual editor
    and its buttons unavailable, with nothing on the page moving; the
    banner box stays available. Unticking gives everything back (#563
    item 6)."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + EDITOR)
    failures = watch(page)
    fields = [
        page.get_by_label("Email subject"),
        page.locator("textarea[name=html]"),
        page.locator("input[name=generate_text]"),
        page.locator("textarea[name=text]"),
    ]
    editor = page.locator("[data-content-editor]")
    bold = page.get_by_role("button", name="Bold", exact=True)
    watched = ["[data-content-editor]", "#content-form button[type=submit]"]
    before = [page.evaluate(PLACE, selector) for selector in watched]
    remove = page.get_by_label("Remove this selected content")
    remove.check()
    for field in fields[:1]:
        greyed(field)
    for field in fields[1:]:
        has_attribute(field, "disabled", "")
    has_attribute(editor, "contenteditable", "false")
    has_attribute(editor, "aria-disabled", "true")
    greyed(bold)
    edit_text = page.get_by_role("button", name="Edit plain text")
    greyed(edit_text)
    available(page.get_by_label("Show the campaign banner at the top of this email"))
    available(page.get_by_role("button", name="Preview changes"))
    after = [page.evaluate(PLACE, selector) for selector in watched]
    for found, expected in zip(after, before, strict=True):
        assert all(abs(a - b) <= 1 for a, b in zip(found, expected, strict=True))
    remove.uncheck()
    available(fields[0])
    has_attribute(editor, "contenteditable", "true")
    assert editor.get_attribute("aria-disabled") is None
    available(bold)
    available(edit_text)
    assert not failures
