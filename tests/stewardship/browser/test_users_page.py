"""The Users page, New and Edit user, in real browsers (#952 slice 5).

One table whose rows stay one line (a long address or Name is cut short with
an ellipsis and shown whole in its title), sorted on the server in place;
Delete through the shared dialog, then an in-place redraw; New user's Email
lookup filling the Name from ParishSoft without overwriting a typed one; and
the only Administrator's lower roles unavailable with the reason. Pages and
POST answers are user_components'.
"""

import json
from urllib.parse import parse_qs

import pytest

from .test_components import axe_violations
from .user_components import (
    EDIT,
    EDIT_ONLY,
    LIST,
    LONG_EMAIL,
    LONG_NAME,
    NAMES,
    NEW,
    NEW_TAKEN,
    SINGLE,
    SUGGESTED,
)
from .waits import eventually, has_attribute, hidden, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)
PAGES = (LIST, SINGLE, NEW, EDIT, EDIT_ONLY, "/user-review")


def rows(page):
    """The Users table's body rows, top to bottom."""
    return page.locator("table.users-table tbody tr")


def emails(page):
    """Each row's address, top to bottom."""
    return [text.strip() for text in rows(page).locator("th").all_inner_texts()]


def watch(page):
    """Fail on any script error, and mark the page so a reload shows."""
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.evaluate("window.notReloaded = true")
    return failures


@pytest.mark.parametrize("width", [320, 1280])
def test_pages_fit_the_screen_and_are_accessible(
    page, component_origin, axe_source, width
):
    """No sideways page scroll and no axe violation, phone width and desktop."""
    page.set_viewport_size({"width": width, "height": 900})
    for path in PAGES:
        page.goto(component_origin + path)
        assert page.evaluate(
            "document.documentElement.scrollWidth <= window.innerWidth"
        ), path
        assert axe_violations(page, axe_source) == [], path


def test_rows_are_one_line_with_ellipsis_and_tooltips(page, component_origin):
    """A long address and Name are cut short, whole in the title and text."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + LIST)
    assert emails(page) == [
        LONG_EMAIL,
        "admin@example.org",
        "leader@example.org",
        "second@example.org",
    ]
    heights = rows(page).evaluate_all(
        "rows => rows.map(row => row.getBoundingClientRect().height)"
    )
    # One line each: every row is as tall as the others, within a pixel.
    assert max(heights) - min(heights) <= 1, heights
    for value in (LONG_EMAIL, LONG_NAME):
        cell = page.locator(f'.cell-ellipsis[title="{value}"]')
        assert cell.text_content() == value
        assert cell.evaluate("node => node.scrollWidth > node.clientWidth")
        assert (
            cell.evaluate("node => getComputedStyle(node).textOverflow") == "ellipsis"
        )
    # Edit is a link to the user's own page; the Administrator can be deleted
    # because another one remains.
    admin = rows(page).nth(1)
    assert (
        admin.get_by_role("link", name="Edit admin@example.org")
        .get_attribute("href")
        .startswith("/admin/users/")
    )
    visible(admin.get_by_role("button", name="Delete admin@example.org"))
    # One identity: Sign-in activity posts it to System logs, never in a URL.
    activity = admin.get_by_role(
        "button", name="Sign-in activity for admin@example.org"
    )
    form = activity.locator("xpath=ancestor::form")
    assert form.get_attribute("method") == "post"
    assert form.get_attribute("action") == "/admin/system/logs/"
    # Several identities: Sign-in activity opens Edit user, which lists each.
    leader = rows(page).nth(2)
    link = leader.get_by_role("link", name="Sign-in activity for leader@example.org")
    assert link.get_attribute("href").endswith("/edit/#user-sign-in-activity")
    # Never signed in.
    assert "Never" in rows(page).nth(3).inner_text()


def test_role_heading_sorts_in_place(page, component_origin):
    """Role sorts by level, Administrators first, without a reload."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + LIST)
    failures = watch(page)
    page.locator('th[data-sort-column="role"] .sort-link').click()
    has_attribute(
        page.locator('th[data-sort-column="role"]'), "aria-sort", "descending"
    )
    eventually(
        page,
        "document.querySelector('table.users-table tbody tr th').textContent"
        ".includes('admin@example.org')",
    )
    assert emails(page)[-1] == "leader@example.org"
    assert "users_sort=-role" in page.url
    assert page.evaluate("window.notReloaded === true")
    assert not failures


def test_delete_asks_names_the_user_and_redraws_in_place(
    page, component_origin, axe_source
):
    """The dialog names address, Name and role; Delete posts one user."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + LIST)
    failures = watch(page)
    delete = page.get_by_role("button", name="Delete leader@example.org")
    identifier = delete.get_attribute("value")
    delete.click()
    dialog = page.locator("dialog[data-confirm-dialog]")
    visible(dialog)
    assert dialog.locator("h2").inner_text() == (
        "Delete leader@example.org (Leader, Lee, Ministry leader)?"
    )
    assert "can no longer sign in" in dialog.inner_text()
    assert axe_violations(page, axe_source) == []
    with page.expect_request(lambda request: request.method == "POST") as sent:
        dialog.get_by_role("button", name="Delete", exact=True).click()
    fields = parse_qs(sent.value.post_data or "")
    assert fields["user_id"] == [identifier]
    assert fields["base_digest"] == ["a" * 64]
    assert set(fields) == {"csrfmiddlewaretoken", "base_digest", "user_id"}
    hidden(dialog)
    eventually(
        page, "document.querySelectorAll('table.users-table tbody tr').length === 3"
    )
    assert "leader@example.org" not in emails(page)
    assert page.evaluate("window.notReloaded === true")
    assert not failures


def test_the_only_administrator_has_no_delete_and_says_why(page, component_origin):
    """The reason line is always reserved; here it holds the reason."""
    page.goto(component_origin + SINGLE)
    admin = rows(page).nth(0)
    assert admin.get_by_role("button", name="Delete admin@example.org").count() == 0
    visible(admin.get_by_role("link", name="Edit admin@example.org"))
    visible(page.get_by_text("The only Administrator can't be deleted.", exact=False))
    page.goto(component_origin + EDIT_ONLY)
    roles = page.get_by_role("group", name="Role")
    assert roles.get_by_label("Administrator").is_checked()
    assert roles.get_by_label("Staff").is_disabled()
    assert roles.get_by_label("Ministry leader").is_disabled()
    visible(page.get_by_text("This is the only Administrator", exact=False))


def test_new_user_fills_the_name_from_parishsoft(page, component_origin):
    """Leaving Email fills the first name, suggests the rest, says so."""
    page.goto(component_origin + NEW)
    failures = watch(page)
    roles = page.get_by_role("group", name="Role")
    assert roles.get_by_label("Ministry leader").is_checked()
    name = page.get_by_label("Name", exact=True)
    help_line = page.locator("[data-names-help]")
    before = help_line.bounding_box()["y"]
    with page.expect_request(lambda request: request.method == "POST") as sent:
        page.get_by_label("Email", exact=True).fill("ann@example.org")
        page.get_by_label("Email", exact=True).press("Tab")
    # The address travels in the POST body, never in a URL.
    assert "ann" not in sent.value.url
    assert parse_qs(sent.value.post_data)["email"] == ["ann@example.org"]
    eventually(
        page,
        "document.querySelector('[data-user-name]').value === "
        + json.dumps(SUGGESTED[0]),
    )
    assert help_line.inner_text() == "Suggested from ParishSoft: Smith, Ann; Smith, Bob"
    assert (
        page.locator("#user-names option").evaluate_all(
            "options => options.map(option => option.value)"
        )
        == SUGGESTED
    )
    # The help line was there all along: nothing below it moved.
    assert help_line.bounding_box()["y"] == before
    # A Name the Administrator typed is never overwritten.
    name.fill("Typed, Name")
    page.get_by_label("Email", exact=True).fill("bob@example.org")
    with page.expect_request(lambda request: request.method == "POST"):
        page.get_by_label("Email", exact=True).press("Tab")
    eventually(
        page,
        "document.querySelector('[data-names-help]').textContent.startsWith('Suggested')",
    )
    assert name.input_value() == "Typed, Name"
    # Review posts the form as the page draws it.
    with page.expect_request(lambda request: request.method == "POST") as review:
        page.get_by_role("button", name="Review").click()
    fields = parse_qs(review.value.post_data)
    assert fields["action"] == ["preview"] and fields["role"] == ["ministry_leader"]
    visible(page.get_by_role("heading", name="Review user change"))
    visible(page.get_by_role("button", name="Apply"))
    assert not failures


def test_an_address_already_a_user_is_marked_at_the_field(page, component_origin):
    """The lookup's error shows at Email and clears on the next edit."""
    page.goto(component_origin + NEW_TAKEN)
    email = page.get_by_label("Email", exact=True)
    email.fill("staff@example.org")
    email.press("Tab")
    has_attribute(page.locator("[data-user-email]"), "aria-invalid", "true")
    error = page.locator("[data-email-error]")
    visible(error)
    assert "already a user" in error.inner_text()
    assert error.get_attribute("id") in email.get_attribute("aria-describedby")
    email.fill("other@example.org")
    hidden(error)
    assert email.get_attribute("aria-invalid") != "true"


def test_edit_shows_the_address_and_each_identity(page, component_origin):
    """Email is shown, not editable; disabled identities are named."""
    page.goto(component_origin + EDIT)
    assert page.locator("input[name=email]").count() == 0
    visible(page.get_by_text("leader@example.org", exact=True).first)
    visible(page.get_by_text("is disabled and can't sign in", exact=False))
    section = page.locator("#user-sign-in-activity")
    assert section.get_by_role("button").count() == 2
    assert page.get_by_label("Name", exact=True).input_value() == "Leader, Lee"


class HeldLookups:
    """New user's lookup answers, held until the test releases each one.

    Each POST to the lookup is kept unanswered, so the page shows its
    "Looking up" state, until ``answer`` fulfills the oldest one.
    """

    def __init__(self, page):
        """Intercept the page's lookup POSTs."""
        self.page, self.routes = page, []
        page.route("**" + NAMES, lambda route: self.routes.append(route))

    def ask(self, email):
        """Leave Email holding ``email``, and wait for its held request."""
        held = len(self.routes)
        field = self.page.get_by_label("Email", exact=True)
        field.fill(email)
        field.press("Tab")
        deadline = 50
        while len(self.routes) == held and deadline:
            self.page.wait_for_timeout(100)
            deadline -= 1
        assert len(self.routes) > held, "the lookup was not sent"

    def answer(self, status, body):
        """Answer the oldest held lookup with ``status`` and JSON ``body``."""
        self.routes.pop(0).fulfill(
            status=status, content_type="application/json", body=json.dumps(body)
        )


def help_says(page, start):
    """Wait until the Name's help line starts with ``start``."""
    eventually(
        page,
        "document.querySelector('[data-names-help]').textContent.startsWith("
        + json.dumps(start)
        + ")",
    )


@pytest.mark.parametrize("width", [320, 1280])
def test_role_choices_never_move_while_the_lookup_answers(
    page, component_origin, width
):
    """Waiting, many names, none, an error and a failure: Role stays put (#736)."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + NEW)
    failures = watch(page)
    roles = page.get_by_role("group", name="Role")
    help_line = page.locator("[data-names-help]")
    place = roles.bounding_box()
    height = help_line.bounding_box()["height"]

    def unmoved(state):
        """The Role fieldset and the help line's height are as they were."""
        now = roles.bounding_box()
        assert (now["x"], now["y"]) == (place["x"], place["y"]), state
        assert help_line.bounding_box()["height"] == height, state
        # The whole words are in the title, whatever the line can show.
        assert help_line.get_attribute("title") == help_line.text_content(), state

    lookups = HeldLookups(page)
    lookups.ask("ann@example.org")
    help_says(page, "Looking up")
    unmoved("waiting")
    many = [f"Montgomery-Fitzwilliam {number}, Bartholomew" for number in range(8)]
    lookups.answer(200, {"error": None, "names": many})
    help_says(page, "Suggested from ParishSoft")
    unmoved("many names")
    # A long answer is cut short with an ellipsis, never wrapped.
    assert help_line.evaluate("node => node.scrollWidth > node.clientWidth")
    assert help_line.evaluate("node => getComputedStyle(node).textOverflow") == (
        "ellipsis"
    )
    lookups.ask("nobody@example.org")
    lookups.answer(200, {"error": None, "names": []})
    help_says(page, "No ParishSoft Member")
    unmoved("no names")
    lookups.ask("staff@example.org")
    lookups.answer(200, {"error": "This address is already a user.", "names": []})
    visible(page.locator("[data-email-error]"))
    unmoved("already a user")
    lookups.ask("busy@example.org")
    lookups.answer(429, {"error": None, "names": []})
    help_says(page, "Couldn't look up")
    unmoved("refused")
    assert not failures


def test_a_filled_name_is_cleared_when_the_address_has_none(page, component_origin):
    """A Name filled for an earlier address goes; a typed Name stays."""
    page.goto(component_origin + NEW)
    failures = watch(page)
    name = page.get_by_label("Name", exact=True)
    lookups = HeldLookups(page)
    lookups.ask("ann@example.org")
    lookups.answer(200, {"error": None, "names": SUGGESTED})
    eventually(
        page,
        "document.querySelector('[data-user-name]').value === "
        + json.dumps(SUGGESTED[0]),
    )
    # No match: the filled Name is cleared.
    lookups.ask("nobody@example.org")
    lookups.answer(200, {"error": None, "names": []})
    help_says(page, "No ParishSoft Member")
    assert name.input_value() == ""
    # A failed lookup clears a filled Name too.
    lookups.ask("ann@example.org")
    lookups.answer(200, {"error": None, "names": SUGGESTED})
    help_says(page, "Suggested from ParishSoft")
    assert name.input_value() == SUGGESTED[0]
    lookups.ask("broken@example.org")
    lookups.answer(503, {"errors": []})
    help_says(page, "Couldn't look up")
    assert name.input_value() == ""
    # An address already a user clears it as well.
    lookups.ask("ann@example.org")
    lookups.answer(200, {"error": None, "names": SUGGESTED})
    help_says(page, "Suggested from ParishSoft")
    lookups.ask("staff@example.org")
    lookups.answer(200, {"error": "This address is already a user.", "names": []})
    visible(page.locator("[data-email-error]"))
    assert name.input_value() == ""
    # A Name the Administrator typed is kept, whatever the answer.
    name.fill("Typed, Name")
    lookups.ask("other@example.org")
    lookups.answer(200, {"error": None, "names": []})
    help_says(page, "No ParishSoft Member")
    assert name.input_value() == "Typed, Name"
    assert not failures
