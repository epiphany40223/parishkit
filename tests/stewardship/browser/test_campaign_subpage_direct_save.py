"""A campaign wizard sub-page's Save and go back saves directly (#1404 PR 4b).

Pages and emails' editor and New and Edit scheduled email as the campaign
wizard draws them (``stepper_components``' ``EDITOR_DIRECT``, ``NEW_DIRECT``
and ``EDIT_DIRECT``): the page draws no Preview changes, review button or
Cancel of its own; the action row holds Return to <step> and Save and go
back, which posts the page's form without a page load and without the
review dialog, follows the change, then opens the step again, whose status
line says what was saved. New and Edit's change gate and browser zone check
hold Save and go back as they held the page's own button, and so does the
editor's change gate, named by its reserved hint. A refusal at the form
stays on the page with its summary and the typed text.
"""

import json
import re
from urllib.parse import parse_qs

import pytest

from .stepper_components import (
    CONTENT,
    EDIT_DIRECT,
    EDITOR_DIRECT,
    EDITOR_DIRECT_REFUSED,
    NEW_DIRECT,
    SCHEDULES,
)
from .test_components import axe_violations
from .test_schedule_list import choose, email_for, fill_new
from .waits import available, greyed, has_text, hidden, same_place, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)


def row(page):
    """The action row's way back and its Save and go back."""
    actions = page.locator("[data-campaign-step-actions]")
    return actions.locator("[data-campaign-step-back]"), actions.locator(
        "[data-campaign-save]"
    )


def watch_posts(page, address, origin, *, gone=False):
    """Note each save's posted values at ``address``; return the list.

    With ``gone``, every load of the page after the first is refused (404),
    as an email's revision page may be once a new revision is saved.
    """
    sent, loads = [], []

    def handle(route):
        """Note a save and let it through; refuse later loads when ``gone``."""
        if route.request.method == "POST":
            sent.append(parse_qs(route.request.post_data or ""))
        else:
            loads.append(route.request.url)
            if gone and len(loads) > 1:
                route.fulfill(status=404, content_type="text/html", body="<p>Gone</p>")
                return
        route.continue_()

    page.route(lambda url: url.split("#")[0] == origin + address, handle)
    return sent


def applied_line(page, saved):
    """Wait for the step's status line: ``saved``, then the time it applied."""
    line = page.locator("#campaign-step-outcome")
    visible(line)
    assert re.fullmatch(
        re.escape(saved) + r" Applied at \d{1,2}:\d{2}\s?[AP]M\.",
        line.inner_text().strip(),
    )


def test_the_editor_saves_and_goes_back(page, component_origin, axe_source):
    """Save and go back saves the editor's text, then Pages and emails opens.

    Pages and emails opens as it is, without reading the editor again: here
    the editor's address is gone once the change is applied.
    """
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    sent = watch_posts(page, EDITOR_DIRECT, component_origin, gone=True)
    page.goto(component_origin + EDITOR_DIRECT)
    back, save = row(page)
    assert back.inner_text() == "Return to Pages and emails"
    visible(save)
    assert save.inner_text() == "Save and go back"
    # The page draws no Preview changes and no Continue of its own.
    assert page.get_by_role("button", name="Preview changes").count() == 0
    assert page.locator("[data-campaign-continue]").count() == 0
    assert axe_violations(page, axe_source) == []
    type_in_editor(page, " and thanks")
    available(save)
    save.click()
    page.wait_for_url(component_origin + CONTENT)
    [body] = sent
    assert body["action"] == ["save"] and "request_key" in body
    assert "and thanks" in body["html"][0]
    applied_line(page, "Welcome page saved.")
    assert failures == []


def type_in_editor(page, text):
    """Type ``text`` at the end of the editor's visual text."""
    page.locator("[data-content-editor]").click()
    page.keyboard.press("End")
    page.keyboard.type(text)


HINT = "content-form-unchanged-hint"


def placed(page, *locators):
    """Each locator's box in page coordinates.

    Typing in the editor may scroll the page (smoothly, so still moving), so
    each box and the scroll offset are read in one script turn.
    """
    return [
        locator.evaluate(
            """(node) => {
                const box = node.getBoundingClientRect();
                return {x: box.x + window.scrollX, y: box.y + window.scrollY,
                        width: box.width, height: box.height};
            }"""
        )
        for locator in locators
    ]


@pytest.mark.parametrize("width", [320, 1280])
def test_the_editor_holds_save_and_go_back_until_a_change(
    page, component_origin, width
):
    """The editor's change gate holds Save and go back, which is outside the
    form, named by its reserved hint; a change releases it and undoing the
    change holds it again. Nothing in the row moves meanwhile, and at 1280
    Return and Save and go back share one line; at 320 the page does not
    scroll sideways."""
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + EDITOR_DIRECT)
    back, save = row(page)
    greyed(save)
    hint = page.locator(f"#{HINT}")
    visible(hint)
    assert hint.inner_text() == "Change the content to save it."
    assert HINT in save.get_attribute("aria-describedby").split()
    before = placed(page, back, save)
    if width == 1280:
        assert same_place(before[0]["y"], before[1]["y"], tolerance=1)
        assert same_place(before[0]["height"], before[1]["height"], tolerance=1)
    assert page.evaluate(
        "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
    )
    type_in_editor(page, " and thanks")
    available(save)
    hidden(hint)
    assert HINT not in (save.get_attribute("aria-describedby") or "").split()
    assert same_place(placed(page, back, save), before, tolerance=1)
    # Undoing the change (deleting what was typed) holds it again.
    for _ in " and thanks":
        page.keyboard.press("Backspace")
    greyed(save)
    visible(hint)
    assert same_place(placed(page, back, save), before, tolerance=1)
    assert failures == []


def test_a_refused_editor_save_stays_with_its_summary(page, component_origin):
    """A save refused at a field (an unknown placeholder) stays on the page:
    the summary and the field's error show, nothing opens, and the editor
    keeps the typed text, so Save and go back can be tried again."""
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))

    def refuse(route):
        """Answer the save with the editor drawn again with its refusal."""
        if route.request.method != "POST":
            route.continue_()
            return
        fetched = route.fetch(
            url=component_origin + EDITOR_DIRECT_REFUSED, method="GET"
        )
        route.fulfill(response=fetched, status=400)

    page.route(
        lambda url: url.split("#")[0] == component_origin + EDITOR_DIRECT, refuse
    )
    page.goto(component_origin + EDITOR_DIRECT)
    _, save = row(page)
    type_in_editor(page, " {{ unknown }}")
    available(save)
    save.click()
    summary = page.locator("[data-error-summary]")
    visible(summary)
    assert "documented placeholders" in summary.inner_text()
    assert page.url == component_origin + EDITOR_DIRECT
    assert page.locator("dialog[open]").count() == 0
    editor = page.locator("[data-content-editor]")
    assert "{{ unknown }}" in editor.inner_text()
    assert "{{ unknown }}" in page.locator('#content-form [name="html"]').input_value()
    available(save)
    assert failures == []


def test_new_scheduled_email_saves_and_goes_back(page, component_origin, axe_source):
    """Save and go back waits for a filled-in form, then saves it directly."""
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    sent = watch_posts(page, NEW_DIRECT, component_origin)
    page.goto(component_origin + NEW_DIRECT)
    back, save = row(page)
    assert back.inner_text() == "Return to Dates and mail schedules"
    # A blank form is nothing to save; the hint says so and names it.
    assert save.is_disabled()
    hint = page.get_by_text("Fill in the scheduled email to save it.")
    visible(hint)
    assert hint.get_attribute("id") in save.get_attribute("aria-describedby")
    assert page.get_by_role("button", name="Add the scheduled email").count() == 0
    assert page.get_by_role("link", name="Cancel").count() == 0
    assert page.locator("dialog#schedule-review").count() == 0
    assert axe_violations(page, axe_source) == []
    fill_new(page, component_origin, path=NEW_DIRECT)
    available(save)
    save.click()
    page.wait_for_url(component_origin + SCHEDULES)
    [body] = sent
    assert body["action"] == ["save"] and "request_key" in body
    assert body["schedule-kind"] == ["initial"]
    assert body["schedule-date"] == ["2054-10-02"]
    applied_line(page, "New scheduled email saved.")
    assert failures == []


def test_edit_scheduled_email_saves_a_change_and_goes_back(page, component_origin):
    """Edit's Save and go back waits for a change, as Review changes did."""
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    sent = watch_posts(page, EDIT_DIRECT, component_origin)
    page.goto(component_origin + EDIT_DIRECT)
    _, save = row(page)
    assert save.is_disabled()
    visible(page.get_by_text("Change the date, time, email or comparison to save it."))
    time = page.locator('[name="schedule-time"]')
    time.fill("1:30 pm")
    time.blur()
    available(save)
    save.click()
    page.wait_for_url(component_origin + SCHEDULES)
    [body] = sent
    assert body["action"] == ["save"] and "request_key" in body
    applied_line(page, "Scheduled email saved.")
    assert failures == []


def test_a_save_send_work_blocks_links_outgoing_mail(page, component_origin):
    """A refusal with a link of its own (send work blocking the change) says
    why in the status line and links that page; the sub-page stays."""
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    refusal = {
        "message": "A scheduled email is being sent right now.",
        "fix": "Wait for it to finish, then save again.",
        "link": {"url": "/admin/deliveries/", "label": "Sent mail"},
    }

    def blocked(route):
        """Answer the save as send work blocking it does (409, JSON)."""
        if route.request.method != "POST":
            route.continue_()
            return
        route.fulfill(
            status=409,
            content_type="application/json",
            body=json.dumps({"refusal": refusal}),
        )

    page.route(lambda url: url.split("#")[0] == component_origin + EDIT_DIRECT, blocked)
    page.goto(component_origin + EDIT_DIRECT)
    _, save = row(page)
    time = page.locator('[name="schedule-time"]')
    time.fill("1:30 pm")
    time.blur()
    available(save)
    save.click()
    line = page.locator("#campaign-step-status")
    has_text(line, f"{refusal['message']} {refusal['fix']} Sent mail")
    link = line.get_by_role("link", name="Sent mail")
    visible(link)
    assert link.get_attribute("href") == "/admin/deliveries/"
    assert page.url == component_origin + EDIT_DIRECT
    available(save)
    assert failures == []


def test_new_without_a_browser_zone_holds_save_and_go_back(page, component_origin):
    """Without a browser zone the send time can't be read: Save and go back,
    outside the form, is held with the reason, as the page's button was."""
    page.add_init_script(
        """{
        const original = Intl.DateTimeFormat.prototype.resolvedOptions;
        Intl.DateTimeFormat.prototype.resolvedOptions = function () {
            return {...original.call(this), timeZone: ZONE};
        };
    }""".replace("ZONE", json.dumps(""))
    )
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.goto(component_origin + NEW_DIRECT)
    visible(page.locator("[data-zone-missing]"))
    _, save = row(page)
    assert save.is_disabled()
    assert "schedule-zone-missing" in save.get_attribute("aria-describedby")
    choose(page, "kind", "initial")
    page.locator('[name="schedule-date"]').fill("2054-10-02")
    page.locator('[name="schedule-time"]').fill("9am")
    choose(page, "template_version", email_for(page, "initial"))
    has_text(page.locator("[data-zone-missing]"), re.compile("time zone"))
    assert save.is_disabled()
    assert failures == []
