"""Signed-in sessions in Chromium and WebKit (#953).

The page lists every live Admin session; its sort headings refresh the table
in place. Signing a session out is a row action (#879): the row's Sign out
icon asks in the shared confirmation dialog, then one POST and the table
redraws in place, with no reload (a mark outside the region survives), and
the change is announced. The reader's own current session has no Sign out.
"""

from urllib.parse import parse_qs

import pytest

from .signed_in_components import PAGE, SORTED
from .waits import eventually, has_text, hidden, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "webkit"], indirect=True
)

MARK = "document.querySelector('h1').dataset.mark = 'kept'"
MARKED = "document.querySelector('h1').dataset.mark"
AXE = """async () => (await axe.run(document, {
    runOnly: {type: 'tag', values: ['wcag2a','wcag2aa','wcag21aa','wcag22aa']}
})).violations.map(({id,impact}) => ({id,impact}))"""


@pytest.mark.parametrize("width", [320, 1280])
def test_the_page_is_accessible_and_never_scrolls_sideways(
    page, component_origin, axe_source, width
):
    """No WCAG 2.2 AA violations, and the table scrolls in its own box."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + PAGE)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.evaluate(axe_source)
    assert page.evaluate(AXE) == []


def test_times_are_browser_local_and_your_row_has_no_sign_out(page, component_origin):
    """Every time is filled in the browser's zone; your own row offers nothing."""
    page.goto(component_origin + PAGE)
    table = page.locator("#sessions-table table")
    eventually(
        page,
        "[...document.querySelectorAll('#sessions-table time')]"
        ".every(t => t.textContent !== t.getAttribute('datetime'))",
    )
    assert "UTC" not in table.locator("tbody").inner_text()
    mine = table.locator("tr").filter(has_text="admin@example.org")
    assert "(you, this browser)" in mine.inner_text()
    assert mine.get_by_role("button").count() == 0
    assert table.locator("thead th").last.inner_text() == "Actions"


def test_a_heading_sorts_the_table_in_place(page, component_origin):
    """User sorts by address, ascending first; nothing reloads."""
    page.goto(component_origin + PAGE)
    page.evaluate(MARK)
    page.locator("#sessions-table thead").get_by_role("link", name="User").click()
    eventually(page, "location.search === '?sort=user'")
    eventually(
        page,
        "document.querySelector('#sessions-table [aria-sort=ascending]') !== null",
    )
    rows = page.locator("#sessions-table tbody th").all_inner_texts()
    assert [row.split(" ")[0] for row in rows] == [
        "admin@example.org",
        "staff@example.org",
        "zed@example.org",
    ]
    assert page.evaluate(MARKED) == "kept"


def test_signing_out_asks_then_acts_in_place(page, component_origin):
    """Sign out asks in the dialog; Cancel changes nothing; confirming posts the
    one session, the table redraws without that row, the mark stays and the
    change is said."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + PAGE)
    page.evaluate(MARK)
    region = page.locator("#sessions-table")
    row = region.locator("tr").filter(has_text="zed@example.org")
    button = row.get_by_role("button", name="Sign out zed@example.org’s session")
    assert button.get_attribute("title") == "Sign out zed@example.org’s session"
    posts = []
    page.on(
        "request",
        lambda request: posts.append(request.url) if request.method == "POST" else None,
    )
    dialog = page.locator("dialog#session-sign-out")
    button.click()
    visible(dialog)
    assert dialog.locator("h2").inner_text() == "Sign out zed@example.org’s session?"
    page.keyboard.press("Escape")
    hidden(dialog)
    assert posts == []
    button.click()
    with page.expect_request(lambda request: request.method == "POST") as sent:
        dialog.get_by_role("button", name="Sign out", exact=True).click()
    fields = parse_qs(sent.value.post_data or "")
    assert set(fields) == {"csrfmiddlewaretoken", "session_id"}
    assert fields["session_id"] == [button.get_attribute("value")]
    has_text(
        page.get_by_role("status").filter(has_text="Signed out zed@example.org"),
        "Signed out zed@example.org’s session.",
    )
    hidden(dialog)
    assert page.evaluate(MARKED) == "kept"
    body = region.locator("tbody")
    assert body.get_by_text("zed@example.org").count() == 0
    assert body.get_by_text("staff@example.org").count() == 1
    assert posts == [component_origin + PAGE]


def test_a_session_that_had_already_ended_is_said_so(page, component_origin):
    """When the session ended before the click, the dialog closes and says it
    had already ended, never "Signed out …"."""
    page.goto(component_origin + SORTED)
    row = page.locator("#sessions-table tr").filter(has_text="zed@example.org")
    row.get_by_role("button", name="Sign out zed@example.org’s session").click()
    dialog = page.locator("dialog#session-sign-out")
    visible(dialog)
    dialog.get_by_role("button", name="Sign out", exact=True).click()
    hidden(dialog)
    message = "That session had already ended; the list is up to date."
    has_text(page.get_by_role("status").filter(has_text="already ended"), message)
    assert page.get_by_role("status").filter(has_text="Signed out").count() == 0
