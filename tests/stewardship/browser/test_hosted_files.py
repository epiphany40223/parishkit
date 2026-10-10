"""Hosted files (#346): copy buttons, the slug suggestion and inline images.

The library is an action table (#879): only unused files offer Edit, Delete
and a selection box; Delete and Delete selected ask in the shared dialog,
then redraw the table in place. The pages and POST answers are
hosted_file_components'.
"""

from urllib.parse import parse_qs

import pytest

from .hosted_file_components import BULK, LIBRARY, REFUSAL, REFUSED
from .test_components import axe_violations
from .waits import eventually, hidden, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)


def test_library_copy_buttons_and_slug_suggestion(page, component_origin, tmp_path):
    """Copy buttons appear with script; choosing a file suggests its slug."""
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.goto(component_origin + "/hosted-files")
    table = page.locator("table.hosted-files")
    visible(table.get_by_text("Sample campaign › Family welcome (page)"))
    field = page.locator("#id_slug")
    picked = tmp_path / "Parish Picnic Flyer (2027).pdf"
    picked.write_bytes(b"%PDF-1.7\n%%EOF\n")
    page.locator("#id_file").set_input_files(str(picked))
    page.wait_for_function(
        "document.querySelector('#id_slug').value === 'parish-picnic-flyer-2027'"
    )
    # A name the Admin typed is never replaced.
    field.fill("my-flyer")
    page.locator("#id_file").set_input_files(str(picked))
    assert field.input_value() == "my-flyer"
    if page.evaluate("Boolean(navigator.clipboard && navigator.clipboard.writeText)"):
        copy = page.locator("button[data-copy]").first
        visible(copy)
    # Without script the fields stay selectable read-only text.
    assert page.locator("input.hosted-file-copy").first.get_attribute("readonly") == ""
    assert not failures


def test_hosted_images_load_inline_under_the_page_policy(page, component_origin):
    """A same-origin hosted image loads under the Family pages' CSP and fits."""
    page.set_viewport_size({"width": 320, "height": 800})
    page.goto(component_origin + "/hosted-image-page")
    image = page.locator(".content-block img")
    visible(image)
    assert image.evaluate("node => node.complete && node.naturalWidth > 0")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def rows(page):
    """The file table's body rows, top to bottom."""
    return page.locator("table.hosted-files tbody tr")


def slugs(page):
    """The placeholder names the table lists, top to bottom."""
    return (
        rows(page)
        .locator("input[readonly][id^=placeholder-]")
        .evaluate_all("fields => fields.map(field => field.value)")
    )


def dialog(page):
    """The library's confirmation dialog."""
    return page.locator("dialog#hosted-file-delete")


def watch(page):
    """Fail on any script error, and mark the page so a reload shows."""
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.evaluate("window.notReloaded = true")
    return failures


def has_count(locator, count):
    """Wait until ``locator`` matches ``count`` elements (a redraw)."""
    from playwright.sync_api import expect

    expect(locator).to_have_count(count)


@pytest.mark.parametrize("width", [320, 1280])
def test_only_unused_files_offer_actions(page, component_origin, axe_source, width):
    """A file in use has no box and no actions; Edit and Delete are equal
    squares named for their file, in the last column."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + LIBRARY)
    failures = watch(page)
    used = rows(page).filter(has_text="ministry-guide")
    assert used.locator("input[type=checkbox]").count() == 0
    assert used.locator(".table-actions").inner_text().strip() == ""
    assert used.get_by_role("button", name="Delete ministry-guide").count() == 0
    picnic = rows(page).filter(has_text="parish-picnic")
    edit = picnic.get_by_role("link", name="Edit parish-picnic")
    delete = picnic.get_by_role("button", name="Delete parish-picnic")
    assert edit.get_attribute("title") == "Edit parish-picnic"
    assert edit.get_attribute("href") == "/hosted-file-name/parish-picnic"
    assert delete.get_attribute("title") == "Delete parish-picnic"
    head = page.locator("table.hosted-files thead th")
    assert head.last.inner_text() == "Actions"
    edit.scroll_into_view_if_needed()
    boxes = [edit.bounding_box(), delete.bounding_box()]
    sizes = [(round(b["width"], 1), round(b["height"], 1)) for b in boxes]
    assert sizes[0] == sizes[1] and sizes[0][0] == sizes[0][1], sizes
    assert abs(boxes[0]["y"] - boxes[1]["y"]) < 0.5, boxes
    # Delete selected waits for a ticked file.
    assert page.get_by_role("button", name="Delete selected").is_disabled()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert axe_violations(page, axe_source) == []
    assert not failures


def test_delete_asks_then_redraws_the_library_in_place(
    page, component_origin, axe_source
):
    """Cancel changes nothing; Delete posts one file and the table redraws."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + LIBRARY)
    failures = watch(page)
    usage = page.locator("#hosted-file-usage")
    assert usage.inner_text().startswith("3 of 100 files, 361.7")
    notice = page.locator("#hosted-file-uploaded")
    assert "Uploaded parish-picnic.png" in notice.inner_text()
    delete = page.get_by_role("button", name="Delete parish-picnic")
    identifier = delete.get_attribute("value")
    delete.click()
    visible(dialog(page))
    assert dialog(page).locator("h2").inner_text() == "Delete parish-picnic?"
    assert "no longer available" in dialog(page).inner_text()
    assert page.evaluate("document.activeElement.textContent.trim()") == "Cancel"
    assert axe_violations(page, axe_source) == []
    dialog(page).get_by_role("button", name="Cancel").click()
    hidden(dialog(page))
    eventually(
        page,
        "document.activeElement.getAttribute('aria-label') === 'Delete parish-picnic'",
    )
    assert rows(page).count() == 3
    delete.click()
    with page.expect_request(lambda request: request.method == "POST") as sent:
        dialog(page).get_by_role("button", name="Delete", exact=True).click()
    fields = parse_qs(sent.value.post_data or "")
    assert fields["file_id"] == [identifier]
    assert set(fields) == {"csrfmiddlewaretoken", "file_id"}
    has_count(rows(page), 2)
    hidden(dialog(page))
    assert slugs(page) == ["{{ file.ministry-guide }}", "{{ file.parish-map }}"]
    # The usage line and the upload notice, outside the table, redraw too.
    assert usage.inner_text().startswith("2 of 100 files, 241.1")
    assert notice.inner_text().strip() == ""
    # The row is gone, so focus lands on the table.
    eventually(page, "document.activeElement.id === 'hosted-file-table'")
    visible(page.locator("main > [role=status]", has_text="Deleted parish-picnic."))
    assert page.evaluate("window.notReloaded === true")
    assert not failures


def test_delete_selected_names_the_count(page, component_origin):
    """Select all ticks only the unused files; the dialog names how many."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + BULK)
    failures = watch(page)
    bulk = page.get_by_role("button", name="Delete selected")
    left = "button => button.getBoundingClientRect().left"
    before = bulk.evaluate(left)
    page.locator("input[data-select-all]").check()
    assert page.locator("input[data-select-row]:checked").count() == 2
    # The count appears after the button, so the button never moves (#563).
    assert bulk.evaluate(left) == before
    bulk.click()
    visible(dialog(page))
    assert dialog(page).locator("h2").inner_text() == "Delete 2 files?"
    with page.expect_request(lambda request: request.method == "POST") as sent:
        dialog(page).get_by_role("button", name="Delete", exact=True).click()
    assert len(parse_qs(sent.value.post_data or "")["file_id"]) == 2
    has_count(rows(page), 1)
    assert slugs(page) == ["{{ file.ministry-guide }}"]
    visible(page.locator("main > [role=status]", has_text="Deleted 2 files."))
    assert page.evaluate("window.notReloaded === true")
    assert not failures


def test_a_refused_delete_explains_why_inside_the_dialog(page, component_origin):
    """The server's reason shows below the buttons; nothing is redrawn."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + REFUSED)
    failures = watch(page)
    page.get_by_role("button", name="Delete parish-map").click()
    accept = dialog(page).get_by_role("button", name="Delete", exact=True)
    before = accept.bounding_box()
    accept.click()
    error = dialog(page).locator("[data-confirm-error]")
    visible(error)
    assert REFUSAL in error.inner_text()
    assert accept.bounding_box() == before
    assert accept.is_disabled()
    assert rows(page).count() == 3
    dialog(page).get_by_role("button", name="Cancel").click()
    hidden(dialog(page))
    assert page.evaluate("window.notReloaded === true")
    assert not failures
