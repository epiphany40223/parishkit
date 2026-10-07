"""The scheduled emails table opens editors in place and keeps past sends read-only."""

import pytest

from .test_components import axe_violations
from .test_schedule_fields import posted
from .waits import hidden, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

PATH = "/schedule-table"


def rows(page):
    """The table's body rows, top to bottom."""
    return page.locator("[data-schedule-table] tbody tr")


def editor(page, label):
    """The editor fieldset whose legend names the schedule ``label``."""
    return page.locator("fieldset[data-schedule-editor]").filter(
        has=page.locator("legend", has_text=label)
    )


@pytest.mark.parametrize("width", [320, 1280])
def test_table_lists_schedules_in_sending_order(
    page, component_origin, axe_source, width
):
    """Rows follow the send order; a past send is muted, with its last send."""
    page.set_viewport_size({"width": width, "height": 900})
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.goto(component_origin + PATH)
    names = rows(page).locator("th").all_inner_texts()
    # An editable row's button reads "Change <name>" (the verb visually
    # hidden), so keep only the name.
    assert [name.split("\n")[-1].strip() for name in names] == [
        "Initial invitation",
        "Reminder 1",
        "Reminder 2",
        "Weekly Admin digest",
    ]
    first = rows(page).nth(0)
    assert first.get_attribute("data-schedule-state") == "sent"
    assert "Sent" in first.inner_text()
    assert "1,031 emails delivered, 2 sends failed" in first.inner_text()
    assert first.get_by_role("link", name="Family email history").count() == 1
    assert first.get_by_role("button").count() == 0
    # Send times are browser-local instants, not raw ISO text.
    when = first.locator("time[data-local-instant]")
    assert when.inner_text() != when.get_attribute("datetime")
    assert "October 1, 2054" in when.inner_text()
    assert "Every Monday" in rows(page).nth(3).inner_text()
    # Every saved editor starts closed; only the blank new row shows, and it
    # reads "New schedule".
    for label in ("Initial invitation", "Reminder 1", "Reminder 2"):
        hidden(editor(page, label))
    visible(page.locator("fieldset[data-schedule-row] legend", has_text="New schedule"))
    # Each saved row keeps its real saved email selected (not an unresolved
    # placeholder), so nothing differs from the server and nothing opens.
    for index in range(4):
        email = page.locator(f'[name="schedules-{index}-template_version"]')
        assert email.input_value()
    assert "unresolved" not in page.inner_text("main")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert axe_violations(page, axe_source) == []
    assert not failures


def test_choosing_a_row_opens_its_editor_in_place(page, component_origin, axe_source):
    """A row click or its button opens that editor; nothing reloads."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + PATH)
    page.evaluate("window.notReloaded = true")
    first = page.get_by_role("button", name="Change Reminder 1")
    assert first.get_attribute("aria-expanded") == "false"
    rows(page).nth(1).locator("td").nth(1).click()
    visible(editor(page, "Reminder 1"))
    assert first.get_attribute("aria-expanded") == "true"
    assert page.evaluate("document.activeElement.id").startswith("schedule-editor-")
    visible(rows(page).nth(1).locator("[data-schedule-editing]"))
    hidden(editor(page, "Reminder 2"))
    page.get_by_role("button", name="Change Reminder 2").click()
    visible(editor(page, "Reminder 2"))
    # The past send has no control, and choosing its row opens nothing.
    rows(page).nth(0).locator("td").first.click()
    hidden(editor(page, "Initial invitation"))
    assert page.evaluate("window.notReloaded") is True
    assert axe_violations(page, axe_source) == []


def test_closed_and_past_editors_still_post_their_saved_values(page, component_origin):
    """The formset is unchanged: every saved row posts, open or not."""
    page.goto(component_origin + PATH)
    page.get_by_role("button", name="Change Reminder 1").click()
    page.locator('[name="schedules-1-date"]').fill("2054-10-11")
    fields = posted(page, component_origin, PATH)
    assert fields["schedules-INITIAL_FORMS"] == "4"
    # A saved mail type is a disabled control, so it never posts; the ID and
    # values of the sent invitation do.
    assert fields["schedules-0-id"]
    assert fields["schedules-0-date"] == "2054-10-01"
    assert fields["schedules-1-date"] == "2054-10-11"
    assert fields["schedules-2-date"] == "2054-10-20"
    assert fields["schedules-3-weekday"] == "0"


def test_a_restored_change_reopens_its_editor_on_pageshow(page, component_origin):
    """A value the browser restored into a closed editor is shown, not hidden."""
    page.goto(component_origin + PATH)
    reminder = editor(page, "Reminder 2")
    hidden(reminder)
    # A restore sets the value without an event, then fires pageshow.
    page.evaluate(
        """document.querySelector('[name="schedules-2-date"]').value = '2054-10-21';
        window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted: true}))"""
    )
    visible(reminder)
    visible(rows(page).nth(2).locator("[data-schedule-editing]"))
    hidden(editor(page, "Reminder 1"))


def test_an_editor_with_a_server_error_starts_open(page, component_origin, axe_source):
    """After a refused preview the row in error is open; the others stay closed."""
    page.goto(component_origin + "/schedule-table-error")
    reminder = editor(page, "Reminder 2")
    visible(reminder)
    assert "Choose a date within the campaign" in reminder.inner_text()
    button = page.get_by_role("button", name="Change Reminder 2")
    assert button.get_attribute("aria-expanded") == "true"
    visible(rows(page).nth(2).locator("[data-schedule-editing]"))
    for label in ("Initial invitation", "Reminder 1"):
        hidden(editor(page, label))
    assert axe_violations(page, axe_source) == []


def test_a_restored_value_in_a_past_send_is_put_back(page, component_origin):
    """A read-only editor never opens, so a restored value is not posted."""
    page.goto(component_origin + PATH)
    page.evaluate(
        """document.querySelector('[name="schedules-0-date"]').value = '2054-10-02';
        window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted: true}))"""
    )
    hidden(editor(page, "Initial invitation"))
    date = page.locator('[name="schedules-0-date"]')
    assert date.input_value() == "2054-10-01"


def test_opening_an_editor_moves_nothing_in_its_row(page, component_origin):
    """At 320px the "(editing below)" note never re-wraps the row (#736).

    The note's space is reserved while idle, so the button and its row keep
    their place on the page when it appears. Positions are page coordinates
    (top plus scrollY), because focusing the editor scrolls the window.
    """
    page.set_viewport_size({"width": 320, "height": 900})
    page.goto(component_origin + PATH)
    button = page.get_by_role("button", name="Change Reminder 1")
    where = """element => {
        const box = element.getBoundingClientRect();
        return [box.top + window.scrollY, box.left, box.height];
    }"""
    before = [button.evaluate(where), rows(page).nth(1).evaluate(where)]
    button.click()
    visible(rows(page).nth(1).locator("[data-schedule-editing]"))
    after = [button.evaluate(where), rows(page).nth(1).evaluate(where)]
    for old, new in zip(before, after, strict=True):
        assert [round(value, 1) for value in old] == [round(value, 1) for value in new]
