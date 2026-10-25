"""Off-site backups' Test access acts in place (#519).

The settings page is served at its real address (backup_test_components),
and the fixture server answers Test access with its view's 303 back to the
page. The test sets a mark outside every region, which only a full page load
could lose, types a folder link it does not save, wheels the page so Test
access is below the fold, and checks the mark, that the page did not jump to
the top, that focus stayed on Test access, the announcement, the running
check, that it settles by itself without a reload (the typed link is kept)
and that exactly one POST was sent. A refused Test access (the view's JSON
answer to a fetch) is shown in place beside the button, never as raw JSON,
and Save still submits natively.
"""

import pytest

from .backup_test_components import (
    BAD_FOLDER,
    BAD_LINK,
    DENIED_SIGNED_IN,
    DENIED_SIGNED_OUT,
    PAGE,
    REFUSED,
    REFUSED_LINKED,
    STATUS,
    TESTED,
)
from .test_export_resolve_in_place import announced, open_marked, stayed, wheel_to
from .test_in_place import MARKED, count_requests
from .waits import focused, hidden, visible

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


LOCAL_REFUSAL = (
    "Off-site Google Drive copies are not available in this local environment. "
    "The local laptop environment reaches Google Drive only with a mail redirect "
    "(deploy --mail-redirect) and the normal clock; until then its backups stay "
    "inside the VM."
)


def test_refused_test_access_is_shown_in_place(page, component_origin):
    """A refused Test access is answered, as the real view answers a fetch,
    with a typed error's JSON (#519: LOCAL's refusal once showed as raw JSON
    in place of the page). Its message and fix show as plain words in one
    alert line after the buttons: no navigation or reload, no JSON text,
    the page and Test access do not move, focus stays on Test access, and
    the POST is not sent again. A second refusal replaces the line."""
    from playwright.sync_api import expect  # imported late: collection needs no browser

    open_marked(page, component_origin, REFUSED)
    posts = count_requests(page, "POST", REFUSED)
    navigations = []
    page.on("framenavigated", lambda frame: navigations.append(frame.url))
    button = page.get_by_role("button", name=NAME, exact=True)
    offset = wheel_to(page, button)
    before = button.bounding_box()
    button.click()
    note = page.locator("[data-in-place-refusal]")
    expect(note).to_have_text(LOCAL_REFUSAL)
    expect(note).to_have_attribute("role", "alert")
    # The line sits after the buttons' row, so neither button moved.
    assert note.evaluate("node => node.previousElementSibling.matches('.actions')")
    assert button.bounding_box() == before
    assert page.evaluate("scrollY") == offset
    focused(button)
    assert page.evaluate(MARKED) == "kept"
    assert page.url == component_origin + REFUSED
    assert navigations == []
    assert '"errors"' not in page.content() and "{" not in note.text_content()
    assert posts == [component_origin + REFUSED]
    button.click()
    expect(note).to_have_count(1)
    page.wait_for_timeout(300)
    expect(note).to_have_count(1)
    expect(note).to_have_text(LOCAL_REFUSAL)
    assert posts == [component_origin + REFUSED] * 2
    assert page.evaluate(MARKED) == "kept"


def test_a_refusal_s_link_is_shown_with_it(page, component_origin):
    """A refusal that names where to fix it shows that link in its line."""
    from playwright.sync_api import expect  # imported late: collection needs no browser

    open_marked(page, component_origin, REFUSED_LINKED)
    page.get_by_role("button", name=NAME, exact=True).click()
    note = page.locator("[data-in-place-refusal]")
    expect(note).to_contain_text("Test access needs Google Workspace mail")
    link = note.get_by_role("link", name="Google Workspace mail")
    workspace = "/admin/system/integrations/google_workspace/"
    expect(link).to_have_attribute("href", workspace)
    assert page.evaluate(MARKED) == "kept"


@pytest.mark.parametrize(
    "address,text",
    [
        (
            DENIED_SIGNED_IN,
            "Your access doesn't include this page. Choose a page from the menu, "
            "or ask a parish administrator if you need this one.",
        ),
        (
            DENIED_SIGNED_OUT,
            "This was not done: you are not allowed to do this now, or your "
            "sign-in has ended. Reload the page.",
        ),
    ],
    ids=["signed_in", "signed_out"],
)
def test_a_denied_test_access_says_why_in_place(page, component_origin, address, text):
    """A ``denied`` refusal's line fits the reader (#2172).

    A session still live is told its access doesn't include this, never to
    sign in again; a request refused before admission keeps the line that
    says the sign-in may have ended. The page is not reloaded either way.
    """
    from playwright.sync_api import expect  # imported late: collection needs no browser

    open_marked(page, component_origin, address)
    page.get_by_role("button", name=NAME, exact=True).click()
    note = page.locator("[data-in-place-refusal]")
    expect(note).to_have_text(text)
    expect(note).to_have_attribute("role", "alert")
    assert note.get_by_role("link").count() == 0
    assert page.url == component_origin + address
    assert page.evaluate(MARKED) == "kept"


def test_save_opens_the_review_dialog_and_test_access_does_not(page, component_origin):
    """Save in the same form opens the review dialog (#750), posting the
    form to the page itself without a page load; Test access, marked
    data-review-skip, stays in place and never opens it."""
    from .review_helpers import answer_reviews
    from .settings_components import PARISH_REVIEW

    reviews = answer_reviews(page, component_origin, PARISH_REVIEW, address=PAGE)
    open_marked(page, component_origin, PAGE)
    dialog = page.locator("dialog#integration-review")
    page.get_by_role("button", name="Save", exact=True).click()
    visible(dialog)
    assert len(reviews) == 1 and "action=preview" in reviews[0]
    dialog.get_by_role("button", name="Cancel").click()
    hidden(dialog)
    page.get_by_role("button", name=NAME, exact=True).click()
    visible(page.locator("#integration-status", has_text="Checking access"))
    assert not dialog.is_visible() and len(reviews) == 1
    assert page.evaluate(MARKED) == "kept"


def test_a_refused_folder_link_is_shown_at_its_field(page, component_origin):
    """Test access with a Drive link that names no folder: the browser's
    pattern admits it, and the view draws the page again with the link's
    error and the error summary (400, #1330). In place, that error is shown
    at the field, in the line the field keeps for it (no list is inserted to
    push the buttons down), with the link the reader typed and one summary,
    and the page is not reloaded."""
    from playwright.sync_api import expect  # imported late: collection needs no browser

    open_marked(page, component_origin, BAD_LINK)
    link = page.get_by_label("Google Drive folder link")
    link.fill(BAD_FOLDER)
    button = page.get_by_role("button", name=NAME, exact=True)
    expect(button).to_be_enabled()
    offset = (
        "node => node.getBoundingClientRect().top"
        " - node.form.getBoundingClientRect().top"
    )
    before = button.evaluate(offset)
    button.click()
    field = page.get_by_label("Google Drive folder link")
    expect(field).to_have_attribute("aria-invalid", "true")
    expect(page.locator("#id_target_error")).to_contain_text("Google Drive folder")
    expect(page.locator("[data-error-summary]")).to_have_count(1)
    assert page.locator(".errorlist[data-review-placed]").count() == 0
    # No list is inserted; only the settings pages' shared error border,
    # 1px thicker on each side of the field, remains. Firefox lays the 2px
    # out on sub-pixel boundaries (2.0000002 in CI), so allow half a pixel;
    # an inserted error line would move the buttons a whole line.
    assert 0 <= button.evaluate(offset) - before <= 2.5
    assert field.input_value() == BAD_FOLDER
    assert page.evaluate(MARKED) == "kept"
