"""Pages and emails' email template tables (#446, #879).

Each mail type's table names the schedules that send each email and has an
Actions column: Edit for every email and, for one no schedule sends, Delete,
which asks in the shared dialog and redraws the tables in place. The pages
and their POST answers are content_catalog_components'.
"""

from urllib.parse import parse_qs

import pytest

from .content_catalog_components import (
    BARE,
    BULK,
    CATALOG,
    FAILED,
    GONE,
    OTHER,
    REFUSAL,
    REFUSED,
    SPARE,
    USED,
)
from .test_components import axe_violations
from .waits import eventually, hidden, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)


def reminders(page):
    """The Reminder table's body rows, top to bottom."""
    return page.get_by_role("table", name="Reminder").locator("tbody tr")


def dialog(page):
    """The open confirmation dialog."""
    return page.locator("dialog[open][data-confirm-dialog]")


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


def posted(request):
    """A captured POST's fields, as lists of values."""
    return parse_qs(request.post_data or "", keep_blank_values=True)


@pytest.mark.parametrize("width", [320, 1280])
def test_email_tables_name_their_senders_and_offer_actions(
    page, component_origin, axe_source, width
):
    """A sent email has Edit only; an unused one also has Delete and a box."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + CATALOG)
    failures = watch(page)
    used, spare, other = reminders(page).all()
    assert "Reminder 1, Reminder 2" in used.inner_text()
    assert "(1a2b3c4d)" in used.inner_text()
    edit = used.get_by_role("link", name="Edit Please respond (1a2b3c4d)")
    assert edit.get_attribute("title") == "Edit Please respond (1a2b3c4d)"
    assert used.get_by_role("button").count() == 0
    assert used.locator("input[type=checkbox]").count() == 0
    assert "No schedule" in spare.inner_text()
    visible(spare.get_by_role("button", name="Delete Please respond (5e6f7a8b)"))
    visible(spare.get_by_role("checkbox", name="Select Please respond (5e6f7a8b)"))
    visible(other.get_by_role("button", name="Delete Last call"))
    # The test page stays a link, named for its email.
    test = spare.get_by_role("link", name="Preview and send test Please respond")
    assert test.get_attribute("href") == "/campaign-mail"
    # The Actions column comes last.
    header = page.get_by_role("table", name="Reminder").locator("thead th")
    assert header.last.inner_text().strip() == "Actions"
    # The confirmation email has no schedules: no Sent by, no Delete, no box.
    confirmation = page.get_by_role("table", name="Confirmation email")
    assert "Sent by" not in confirmation.inner_text()
    visible(confirmation.get_by_role("link", name="Edit Thank you"))
    assert confirmation.get_by_role("button").count() == 0
    assert confirmation.locator("input[type=checkbox]").count() == 0
    assert page.get_by_role("button", name="Delete selected").count() == 1
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert axe_violations(page, axe_source) == []
    assert not failures


@pytest.mark.parametrize("width", [320, 1280])
def test_row_action_icons_are_the_same_size(page, component_origin, width):
    """Edit and Delete draw as equal squares side by side (#879 pattern)."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + CATALOG)
    row = reminders(page).nth(2)
    edit = row.get_by_role("link", name="Edit Last call").bounding_box()
    delete = row.get_by_role("button", name="Delete Last call").bounding_box()
    sizes = [(round(b["width"], 1), round(b["height"], 1)) for b in (edit, delete)]
    assert sizes[0] == sizes[1], sizes
    assert sizes[0][0] == sizes[0][1], sizes
    assert abs(edit["y"] - delete["y"]) < 0.5, (edit, delete)


def test_delete_asks_in_a_dialog_then_redraws_the_tables_in_place(
    page, component_origin, axe_source
):
    """Cancel changes nothing; Delete posts one ID and the tables redraw."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + CATALOG)
    failures = watch(page)
    name = "Delete Please respond (5e6f7a8b)"
    delete = page.get_by_role("button", name=name)
    delete.click()
    visible(dialog(page))
    assert (
        dialog(page).locator("h2").inner_text() == "Delete Please respond (5e6f7a8b)?"
    )
    assert "never changed" in dialog(page).inner_text()
    assert page.evaluate("document.activeElement.textContent.trim()") == "Cancel"
    page.keyboard.press("Tab")
    assert page.evaluate("Boolean(document.activeElement.closest('dialog'))")
    assert axe_violations(page, axe_source) == []
    page.keyboard.press("Escape")
    hidden(dialog(page))
    eventually(page, f"document.activeElement.getAttribute('aria-label') === '{name}'")
    assert reminders(page).count() == 3
    scroll = page.evaluate("window.scrollY")
    delete.click()
    with page.expect_request(lambda request: request.method == "POST") as sent:
        dialog(page).get_by_role("button", name="Delete", exact=True).click()
    fields = posted(sent.value)
    assert fields["revision_id"] == [SPARE]
    assert fields["base_digest"] == ["a" * 64]
    assert "csrfmiddlewaretoken" in fields
    has_count(reminders(page), 2)
    hidden(page.locator("dialog[open]"))
    assert page.locator(f'button[value="{SPARE}"]').count() == 0
    # The row that opened it is gone: focus goes to its table's heading.
    eventually(page, "document.activeElement.id === 'email-reminder-title'")
    visible(
        page.locator(
            "main > [role=status]", has_text="Deleted Please respond (5e6f7a8b)."
        )
    )
    assert abs(page.evaluate("window.scrollY") - scroll) < 50
    assert page.evaluate("window.notReloaded === true")
    assert not failures


def test_delete_selected_stays_unavailable_until_a_box_is_ticked(
    page, component_origin
):
    """Only unused emails can be ticked; the dialog names how many go."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + BULK)
    failures = watch(page)
    bulk = page.get_by_role("button", name="Delete selected")
    assert bulk.is_disabled()
    before = bulk.bounding_box()
    boxes = page.locator("input[data-select-row]")
    assert boxes.count() == 2
    boxes.nth(0).check()
    assert not bulk.is_disabled()
    # Ticking never moves the button.
    assert bulk.bounding_box() == before
    boxes.nth(1).check()
    bulk.click()
    visible(dialog(page))
    assert dialog(page).locator("h2").inner_text() == "Delete 2 emails?"
    with page.expect_request(lambda request: request.method == "POST") as sent:
        dialog(page).get_by_role("button", name="Delete", exact=True).click()
    assert sorted(posted(sent.value)["revision_id"]) == sorted([SPARE, OTHER])
    assert USED not in posted(sent.value)["revision_id"]
    has_count(reminders(page), 1)
    # No email left to delete: no selection column or bulk bar.
    assert page.get_by_role("button", name="Delete selected").count() == 0
    visible(page.locator("main > [role=status]", has_text="Deleted 2 emails."))
    assert page.evaluate("window.notReloaded === true")
    assert not failures


def test_a_refused_delete_explains_why_inside_the_dialog(page, component_origin):
    """The server's reason shows below the buttons; nothing is redrawn."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + REFUSED)
    failures = watch(page)
    page.get_by_role("button", name="Delete Last call").click()
    accept = dialog(page).get_by_role("button", name="Delete", exact=True)
    before = accept.bounding_box()
    accept.click()
    error = dialog(page).locator("[data-confirm-error]")
    visible(error)
    assert REFUSAL in error.inner_text()
    assert accept.bounding_box() == before
    assert accept.is_disabled()
    assert reminders(page).count() == 3
    dialog(page).get_by_role("button", name="Cancel").click()
    hidden(page.locator("dialog[open]"))
    assert page.evaluate("window.notReloaded === true")
    assert not failures


def test_a_change_that_did_not_apply_is_reported(page, component_origin):
    """A recorded change that failed says so and links its status."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + FAILED)
    failures = watch(page)
    page.get_by_role("button", name="Delete Last call").click()
    dialog(page).get_by_role("button", name="Delete", exact=True).click()
    error = dialog(page).locator("[data-confirm-error]")
    visible(error)
    assert "Not applied" in error.inner_text()
    visible(error.get_by_role("link", name="See the change's status"))
    assert reminders(page).count() == 3
    assert not failures


def test_a_bare_400_says_to_reload_not_to_wait(page, component_origin):
    """A refusal with no explanation of its own (a 400 for a field the page
    should not have sent) asks for a reload, never "try again in a moment"
    as if the server were down (#1401)."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + BARE)
    failures = watch(page)
    page.get_by_role("button", name="Delete Last call").click()
    dialog(page).get_by_role("button", name="Delete", exact=True).click()
    error = dialog(page).locator("[data-confirm-error]")
    visible(error)
    text = error.inner_text()
    assert "the page sent something the server did not expect" in text
    assert "Reload the page, then try again." in text
    assert "right now" not in text
    assert reminders(page).count() == 3
    assert not failures


@pytest.mark.parametrize("status", [404, 410])
def test_a_bare_404_or_410_says_it_no_longer_exists(page, component_origin, status):
    """A gone item with no explanation of its own says so and to reload,
    never "try again in a moment" (#1400)."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + GONE.format(status))
    failures = watch(page)
    page.get_by_role("button", name="Delete Last call").click()
    dialog(page).get_by_role("button", name="Delete", exact=True).click()
    error = dialog(page).locator("[data-confirm-error]")
    visible(error)
    text = error.inner_text()
    assert "This was not done: it no longer exists. Reload the page." in text
    assert "right now" not in text
    assert reminders(page).count() == 3
    assert not failures
