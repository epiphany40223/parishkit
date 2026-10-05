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


FUTURE = "The contact attempt's date and time can't be in the future."


def contact_marked(page, marked):
    """Assert that Date and Time are (or are not) marked in error, with the
    one contact message shown (or hidden) beside them."""
    message = page.locator("#followup-item #contact-error")
    for field in ("#contact-date", "#contact-time"):
        state = page.locator(field).get_attribute("aria-invalid")
        assert state == ("true" if marked else "false") or (not marked and not state)
        described = page.locator(field).get_attribute("aria-describedby") or ""
        assert ("contact-error" in described.split()) == marked
    if marked:
        has_text(message, FUTURE)
        assert page.locator(".errorlist:visible").count() == 1
    else:
        hidden(message)


def test_server_future_refusal_is_shown_at_its_fields(page, component_origin):
    """The server's refusal of a future contact time is swapped in at the
    fields, once, and its summary links to the date and takes focus, as
    every Admin refusal summary does. Leaving a field without editing it
    keeps the mark; correcting the date clears the mark, the message and
    the summary at once, before any save, and the save then goes through."""
    page.set_viewport_size(VIEWPORT)
    page.goto(component_origin + FOLLOWUP_ITEM)
    page.evaluate(MARK)
    scroll_below(page, FOLLOWUP_SAVE)
    answer_with(page, component_origin, FOLLOWUP_UPDATE, 400, "/followup-item-future")
    page.get_by_label("How").select_option("phone")
    page.locator("#contact-date").fill("2026-09-19")
    page.locator("#contact-time").fill("10:00")
    page.locator(FOLLOWUP_SAVE).click()
    summary = page.locator("#followup-item > [data-error-summary]:first-child")
    link = summary.get_by_role("link", name=FUTURE)
    visible(link)
    assert page.evaluate(MARKED) == "kept"
    contact_marked(page, True)
    for field in ("#contact-date", "#contact-time"):
        # The zone note (#558) still describes it, then the error.
        assert page.locator(field).get_attribute("aria-describedby") == (
            "contact-zone-help contact-error"
        )
    assert (
        page.evaluate("document.querySelector('#contact-time').nextElementSibling.id")
        == "contact-error"
    )
    assert link.get_attribute("href") == "#contact-date"
    assert page.evaluate("document.activeElement.hasAttribute('data-error-summary')")
    link.click()
    assert page.evaluate("document.activeElement.id") == "contact-date"
    # Leaving the field without editing it keeps the mark. (Tab would only
    # move between a date input's own parts, so focus another field.)
    page.locator("#contact-notes").focus()
    contact_marked(page, True)
    # Corrected: cleared at once, summary and all, before saving.
    page.unroute(component_origin + FOLLOWUP_UPDATE)
    page.locator("#contact-date").fill("2026-09-19")
    contact_marked(page, False)
    assert page.locator("[data-error-summary]").count() == 0
    assert page.locator(FOLLOWUP_SAVE).is_enabled()
    page.locator(FOLLOWUP_SAVE).click()
    visible(page.locator("#followup-item").get_by_text("Saved <note>", exact=True))
    assert page.locator(".errorlist:visible, [aria-invalid=true]").count() == 0


def test_hiding_the_contact_group_clears_its_marks(page, component_origin):
    """Choosing no contact attempt hides Date and Time, which are then not
    sent, so a server error marked on them clears with its summary item."""
    page.goto(component_origin + FOLLOWUP_ITEM)
    answer_with(page, component_origin, FOLLOWUP_UPDATE, 400, "/followup-item-future")
    page.get_by_label("How").select_option("phone")
    page.locator("#contact-date").fill("2026-09-19")
    page.locator("#contact-time").fill("10:00")
    page.locator(FOLLOWUP_SAVE).click()
    visible(page.locator("[data-error-summary]"))
    page.get_by_label("How").select_option("")
    assert page.locator("[data-error-summary]").count() == 0
    for field in ("#contact-date", "#contact-time"):
        assert page.locator(field).get_attribute("aria-invalid") == "false"
    page.get_by_label("How").select_option("phone")
    contact_marked(page, False)
    assert page.locator(FOLLOWUP_SAVE).is_enabled()


def test_django_choice_group_error_clears_together(page, component_origin):
    """A Django choice group (RadioSelect, drawn as a fieldset described by
    its error, with each input marked invalid) clears as one on the first
    choice: every input's mark and the message on the fieldset."""
    page.goto(component_origin + "/field-error-group")
    message = page.locator("#id_kind_error")
    visible(message)
    radios = page.locator("input[name=kind]")
    assert radios.evaluate_all(
        "nodes => nodes.map((node) => node.getAttribute('aria-invalid'))"
    ) == ["true", "true"]
    page.get_by_label("Second").check()
    hidden(message)
    assert radios.evaluate_all(
        "nodes => nodes.map((node) => node.getAttribute('aria-invalid'))"
    ) == ["false", "false"]
    assert page.locator("fieldset").get_attribute("aria-describedby") is None


def test_server_only_error_clears_when_its_field_is_edited(page, component_origin):
    """An error only the server can check (an outcome that doesn't fit the
    request) clears from its field, with its message and its summary item,
    on the first edit of that field; the server checks again on save."""
    page.goto(component_origin + FOLLOWUP_ITEM)
    answer_with(page, component_origin, FOLLOWUP_UPDATE, 200, "/followup-item-refused")
    page.get_by_label("Status").select_option("resolved")
    page.get_by_label("Outcome", exact=True).select_option("joined")
    page.locator(FOLLOWUP_SAVE).click()
    outcome = page.locator("#followup-outcome")
    message = page.locator("#followup-outcome-error")
    has_text(message, "Left ministry doesn't apply to a request to join.")
    assert outcome.get_attribute("aria-invalid") == "true"
    assert outcome.get_attribute("aria-describedby") == "followup-outcome-error"
    assert outcome.evaluate("node => getComputedStyle(node).borderTopWidth") == "2px"
    outcome.select_option("joined")
    assert outcome.get_attribute("aria-invalid") == "false"
    assert outcome.get_attribute("aria-describedby") is None
    hidden(message)
    assert page.locator("[data-error-summary]").count() == 0


@pytest.mark.parametrize(
    ("path", "server_only", "browser_checked"),
    [
        ("/parish-settings-invalid", "#id_online_giving_url", "#id_name"),
        ("/setup-parish-invalid", "#id_website", None),
    ],
)
def test_django_form_errors_clear_when_edited(
    page, component_origin, path, server_only, browser_checked
):
    """Django-rendered forms (settings, the setup wizard) follow the same
    rule: a field the server marked clears its border and its message on
    the first edit, and leaves every other mark alone."""
    page.goto(component_origin + path)
    field = page.locator(server_only)
    assert field.get_attribute("aria-invalid") == "true"
    message = page.locator(f"{server_only}_error")
    visible(message)
    assert f"{server_only[1:]}_error" in field.get_attribute("aria-describedby")
    field.fill("https://giving.example.org")
    assert field.get_attribute("aria-invalid") == "false"
    hidden(message)
    assert f"{server_only[1:]}_error" not in (
        field.get_attribute("aria-describedby") or ""
    )
    if browser_checked:
        other = page.locator(browser_checked)
        assert other.get_attribute("aria-invalid") == "true"
        visible(page.locator(f"{browser_checked}_error"))
        other.fill("Sample Parish")
        assert other.get_attribute("aria-invalid") == "false"
        hidden(page.locator(f"{browser_checked}_error"))


def test_browser_marked_field_clears_once_valid(page, component_origin):
    """A field the browser marked on leaving it (a required field left
    empty) clears as soon as its value is valid, without leaving it."""
    page.goto(component_origin + "/parish-settings")
    name = page.locator("#id_name")
    name.fill("")
    name.blur()
    assert name.get_attribute("aria-invalid") == "true"
    name.focus()
    name.type("S")
    assert name.get_attribute("aria-invalid") == "false"


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
    # Completion stays ticked, so the reopen confirmation is not sent.
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


def test_information_reopen_needs_the_confirmation(page, component_origin):
    """The confirmation to reopen completed follow-up shows only once
    "Follow-up completed" is unticked, and Save waits for it, saying why;
    the same holds for the form a save swaps in. (Without it the server
    refuses the save with its plain error page.)"""
    page.goto(component_origin + INFORMATION_ITEM)
    for stage in ("loaded", "swapped"):
        completed = page.get_by_label("Follow-up completed")
        confirm = page.get_by_label("If clearing completion", exact=False)
        save = page.locator(INFORMATION_SAVE)
        hint = page.locator("#information-save-hint")
        hidden(confirm)
        assert confirm.is_disabled() and save.is_enabled(), stage
        completed.uncheck()
        visible(confirm)
        assert save.is_disabled(), stage
        has_text(hint, "Tick the confirmation to reopen this follow-up.")
        confirm.check()
        assert save.is_enabled() and not hint.is_visible(), stage
        completed.check()
        hidden(confirm)
        assert save.is_enabled(), stage
        if stage == "loaded":
            save.click()
            visible(page.get_by_role("heading", name="Version 3"))
