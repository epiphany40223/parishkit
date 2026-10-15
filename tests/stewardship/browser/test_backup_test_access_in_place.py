"""Off-site backups' Test access acts in place (#519).

The settings page is served at its real address (backup_test_components),
and the fixture server answers Test access with its view's 303 back to the
page. The test sets a mark outside every region, which only a full page load
could lose, types a folder link it does not save, wheels the page so Test
access is below the fold, and checks the mark, that the page did not jump to
the top, that focus stayed on Test access, the announcement, the running
check, that it settles by itself without a reload (the typed link is kept)
and that exactly one POST was sent. A refused Test access (400) is shown
whole and sent once, and Save still submits natively.
"""

import pytest

from .backup_test_components import PAGE, REFUSED, STATUS, TESTED
from .test_export_resolve_in_place import announced, open_marked, stayed, wheel_to
from .test_in_place import MARKED, count_requests
from .waits import focused, painted, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

NAME = "Test access"
TYPED = "https://drive.google.com/drive/folders/typed-not-saved"


def test_test_access_runs_and_settles_in_place(page, component_origin):
    """Test access posts once (a double click included), follows the view's
    redirect back to the page and swaps in its running status line, with
    focus kept on Test access. The line then settles by itself, and its
    quiet follow-up re-reads the page's status line in place, so the folder
    link typed but not saved is kept."""
    from playwright.sync_api import expect  # imported late: collection needs no browser

    open_marked(page, component_origin, PAGE)
    polls = count_requests(page, "GET", STATUS)
    page.get_by_label("Google Drive folder link").fill(TYPED)
    posts = count_requests(page, "POST", PAGE)
    button = page.get_by_role("button", name=NAME, exact=True)
    offset = wheel_to(page, button)
    button.dblclick()
    announced(page, "Access check started.")
    stayed(page, offset)
    focused(button)
    visible(page.locator("#integration-status", has_text="Checking access"))
    assert page.url == component_origin + TESTED + "#integration-status"
    # live-status-v1.js polls after its first delay (2 s).
    expect(page.locator("#integration-status")).to_contain_text(
        "Access works.", timeout=10000
    )
    expect(page.locator("#integration-status [data-live-pending]")).to_have_count(0)
    page.wait_for_timeout(500)
    assert polls
    focused(button)
    assert page.get_by_label("Google Drive folder link").input_value() == TYPED
    assert posts == [component_origin + PAGE]
    assert page.evaluate(MARKED) == "kept"


def test_refused_test_access_is_shown_once(page, component_origin):
    """A refused Test access (the view's error page, which draws no status
    line) is shown whole, as a native submission shows it, never swallowed,
    and is not sent again."""
    open_marked(page, component_origin, REFUSED)
    posts = count_requests(page, "POST", REFUSED)
    button = page.get_by_role("button", name=NAME, exact=True)
    wheel_to(page, button)
    button.click()
    visible(page.get_by_role("heading", name="Enter the Google Drive folder link"))
    painted(page)
    assert posts == [component_origin + REFUSED]


def test_save_still_submits_natively(page, component_origin):
    """Save in the same form is marked data-in-place-native: it submits as
    a native form does, so its answer loads in place of this page."""
    open_marked(page, component_origin, PAGE)
    with page.expect_navigation():
        page.get_by_role("button", name="Save", exact=True).click()
    assert page.evaluate(MARKED) is None
