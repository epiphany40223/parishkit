"""Save follow-up acts in place on both follow-up pages (#519 PR 2).

The Ministry follow-up request page and the additional-information item page
are served at their real addresses, and the fixture server answers each Save
with a real Post/Redirect/Get redirect back to the page (followup_components
and information_components). Each test sets a mark outside every region,
which only a full page load could lose, as test_in_place.py does.
"""

import pytest

from .followup_components import GATE_ITEM, RESOLVE_ITEM
from .followup_components import ITEM as FOLLOWUP_ITEM
from .followup_components import SAVED as FOLLOWUP_SAVED
from .followup_components import UPDATE as FOLLOWUP_UPDATE
from .information_components import ITEM as INFORMATION_ITEM
from .information_components import SAVED as INFORMATION_SAVED
from .information_components import UPDATE as INFORMATION_UPDATE
from .test_in_place import MARK, MARKED, VIEWPORT, count_requests, scroll_below
from .waits import has_text, hidden, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

FOLLOWUP_SAVE = "#followup-item form[data-in-place] button[type=submit]"
INFORMATION_SAVE = "#information-item form[data-in-place] button[type=submit]"
FOCUSED_SAVE = (
    "document.activeElement.matches(selector) && document.activeElement.isConnected"
)


def answer_with(page, component_origin, url, status, fixture):
    """Answer the next POST to ``url`` with the fixture page at ``fixture``,
    unredirected, as the view answers a refusal or a conflict."""
    body = page.request.get(component_origin + fixture).text()
    page.route(
        component_origin + url,
        lambda route: route.fulfill(
            status=status, content_type="text/html; charset=utf-8", body=body
        ),
    )


def in_viewport(page, selector):
    """Whether the element matching ``selector`` is at least partly on screen."""
    return page.evaluate(
        """selector => {
        const box = document.querySelector(selector).getBoundingClientRect();
        return box.bottom > 0 && box.top < window.innerHeight;
    }""",
        selector,
    )


def test_followup_save_is_in_place(page, component_origin):
    """Save follow-up posts once, follows the redirect back to the request,
    and swaps the request panel: no reload, the reader's place kept, focus on
    the fresh Save button, "Follow-up saved." announced, and the address set
    to the redirect's page. The fresh form's conditional fields and its Save
    gate still work."""
    page.set_viewport_size(VIEWPORT)
    page.goto(component_origin + FOLLOWUP_ITEM)
    page.evaluate(MARK)
    offset = scroll_below(page, FOLLOWUP_SAVE)
    posts = count_requests(page, "POST", "/update")
    page.get_by_label("Status").select_option("in_progress")
    page.locator(FOLLOWUP_SAVE).dblclick()
    visible(page.locator("#followup-item").get_by_text("Saved <note>", exact=True))
    assert page.evaluate(MARKED) == "kept"
    assert abs(page.evaluate("window.scrollY") - offset) < 40
    assert page.evaluate(
        f"selector => ({FOCUSED_SAVE})", "#followup-item button[type=submit]"
    )
    has_text(
        page.get_by_role("status").filter(has_text="Follow-up saved."),
        "Follow-up saved.",
    )
    assert page.url == component_origin + FOLLOWUP_SAVED + "#followup-item"
    assert posts == [component_origin + FOLLOWUP_UPDATE]
    # The swapped-in form still shows only the fields that apply, and Save
    # still waits for them.
    save = page.locator(FOLLOWUP_SAVE)
    page.get_by_label("How").select_option("phone")
    visible(page.locator("#contact-date"))
    assert save.is_disabled()
    has_text(
        page.locator("#followup-save-hint"),
        "Enter the date and time of the contact attempt to save.",
    )
    page.get_by_label("How").select_option("")
    hidden(page.locator("#contact-date"))
    assert save.is_enabled()
    page.get_by_label("Status").select_option("resolved")
    visible(page.get_by_label("Outcome", exact=True))
    assert save.is_disabled()


@pytest.mark.parametrize("start", [RESOLVE_ITEM, GATE_ITEM])
def test_followup_save_without_a_usable_save_focuses_the_heading(
    page, component_origin, start
):
    """A save that resolves the request comes back with no form, and one
    made while other campaign work starts comes back with Save disabled.
    Either way focus cannot return to Save, so it goes to the request's
    heading (made focusable), not the unnamed panel, and the save is still
    announced, without a reload and with one POST."""
    page.set_viewport_size(VIEWPORT)
    page.goto(component_origin + start)
    page.evaluate(MARK)
    scroll_below(page, FOLLOWUP_SAVE)
    posts = count_requests(page, "POST", "/update")
    page.get_by_label("Status").select_option("resolved")
    page.get_by_label("Outcome", exact=True).select_option("joined")
    page.locator(FOLLOWUP_SAVE).click()
    has_text(
        page.get_by_role("status").filter(has_text="Follow-up saved."),
        "Follow-up saved.",
    )
    if start == RESOLVE_ITEM:
        visible(page.get_by_text("This request is closed.", exact=False))
        assert page.locator("#followup-item form").count() == 0
    else:
        assert page.locator(FOLLOWUP_SAVE).is_disabled()
    assert page.evaluate(MARKED) == "kept"
    heading = "#followup-item > h2:first-of-type"
    assert page.evaluate(
        "selector => document.activeElement === document.querySelector(selector)",
        heading,
    )
    assert page.locator(heading).get_attribute("tabindex") == "-1"
    assert posts == [component_origin + start + "update"]


def test_followup_refusal_is_swapped_into_the_request(page, component_origin):
    """A correctable refusal (#553) re-renders the request page at the update
    address, unredirected; it is swapped into the request panel (#562): no
    reload, one POST, the summary at the top of the panel with focus and on
    screen, its message announced, the values the server kept, and the
    address unchanged. The refused form's conditional fields and its Save gate
    still work after the swap."""
    page.set_viewport_size(VIEWPORT)
    page.goto(component_origin + FOLLOWUP_ITEM)
    page.evaluate(MARK)
    scroll_below(page, FOLLOWUP_SAVE)
    answer_with(page, component_origin, FOLLOWUP_UPDATE, 200, "/followup-item-refused")
    posts = count_requests(page, "POST", "/update")
    page.get_by_label("Status").select_option("resolved")
    page.get_by_label("Outcome", exact=True).select_option("joined")
    page.locator(FOLLOWUP_SAVE).click()
    summary = page.locator("#followup-item > [data-error-summary]:first-child")
    message = "Left ministry doesn't apply to a request to join."
    has_text(summary.get_by_role("link"), message)
    assert page.evaluate(MARKED) == "kept"
    assert page.locator("[data-error-summary]").count() == 1
    assert page.evaluate("document.activeElement.hasAttribute('data-error-summary')")
    assert in_viewport(page, "#followup-item > [data-error-summary]")
    has_text(page.get_by_role("status").filter(has_text=message), message)
    assert page.url == component_origin + FOLLOWUP_ITEM
    assert posts == [component_origin + FOLLOWUP_UPDATE]
    # The values the server re-rendered, with their fields shown.
    outcome = page.get_by_label("Outcome", exact=True)
    visible(outcome)
    assert outcome.input_value() == "other"
    assert page.get_by_label("What happened").input_value() == "Kept <reply>"
    summary.get_by_role("link").click()
    assert page.evaluate("document.activeElement.id") == "followup-outcome"
    save = page.locator(FOLLOWUP_SAVE)
    assert save.is_enabled()
    outcome.select_option("")
    assert save.is_disabled()
    has_text(page.locator("#followup-save-hint"), "Choose an outcome to save.")
    page.get_by_label("Status").select_option("in_progress")
    hidden(outcome)
    assert save.is_enabled()


def test_followup_conflict_is_shown_whole_and_sent_once(page, component_origin):
    """A stale save (409) answers with the conflict page, which lacks the
    request panel, so it is shown whole, as a native submission shows it,
    and the POST is never sent again."""
    page.goto(component_origin + FOLLOWUP_ITEM)
    answer_with(page, component_origin, FOLLOWUP_UPDATE, 409, "/followup-error-409")
    posts = count_requests(page, "POST", "/update")
    page.locator(FOLLOWUP_SAVE).click()
    has_text(page.locator("h1"), "Ministry follow-up unavailable")
    visible(page.get_by_text("This request changed. Nothing was saved.", exact=False))
    assert posts == [component_origin + FOLLOWUP_UPDATE]


def test_information_save_is_in_place(page, component_origin):
    """Save follow-up on an information item posts once, follows the
    redirect back to the item and swaps the item panel and its history: no
    reload, the reader's place kept, focus on the fresh Save button, the
    save announced and the address set to the redirect's page."""
    page.set_viewport_size(VIEWPORT)
    page.goto(component_origin + INFORMATION_ITEM)
    page.evaluate(MARK)
    offset = scroll_below(page, INFORMATION_SAVE)
    posts = count_requests(page, "POST", "/update")
    with page.expect_request(lambda request: request.method == "POST") as sent:
        page.locator(INFORMATION_SAVE).dblclick()
    # The reopen confirmation, left unticked, is not sent.
    assert "confirm_clear" not in sent.value.post_data
    assert "followed_up=yes" in sent.value.post_data
    visible(page.get_by_role("heading", name="Version 3"))
    assert page.get_by_label("Staff notes").input_value() == "Saved <note>"
    assert page.evaluate(MARKED) == "kept"
    assert abs(page.evaluate("window.scrollY") - offset) < 40
    assert page.evaluate(
        f"selector => ({FOCUSED_SAVE})", "#information-item button[type=submit]"
    )
    has_text(
        page.get_by_role("status").filter(has_text="Follow-up saved."),
        "Follow-up saved.",
    )
    assert page.url == component_origin + INFORMATION_SAVED + "#information-item"
    assert posts == [component_origin + INFORMATION_UPDATE]
