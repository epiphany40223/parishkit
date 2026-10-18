"""A campaign wizard step's Save and continue saves directly (#1404 PR 4).

Share options as the campaign wizard draws it (``stepper_components.DIRECT``):
the action row's one primary reads Continue to the next step while the form
holds no change and Save and continue once it does, in the same place. Save
and continue posts the form without a page load and without the review
dialog, follows the change, then opens the next step not done yet, whose
status line says what was saved. A refusal stays on the page, said in the
row's status line, or at its field. A change recorded but not applied
keeps the page, drawn again with a new request key, so a later save is a
request of its own (#1486 review). Campaign settings saves the same way.
"""

import json
import re
from urllib.parse import parse_qs
from uuid import uuid4

import pytest

from .stepper_components import (
    CONTENT,
    DIRECT,
    DIRECT_RETRY,
    DIRECT_SETTINGS,
    DIRECT_SETTINGS_REFUSED,
)
from .test_components import axe_violations
from .waits import available, has_attribute, has_text, hidden, same_place, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)


def primary(page):
    """The action row's Continue, its Save and continue, and their cell."""
    row = page.locator("[data-campaign-step-actions]")
    return (
        row.locator("[data-campaign-continue] button"),
        row.locator("[data-campaign-save]"),
        row.locator("[data-campaign-primary]"),
    )


def watch_posts(page, address, origin, *, renew=False):
    """Note each save's posted values at ``address``; return the list.

    With ``renew``, every load of the page after the first draws a new
    request key, as the server draws one each time it draws the page (the
    fixture server's page is fixed).
    """
    sent, loads = [], []

    def handle(route):
        """Note a save; let it and the loads through (renewing the key)."""
        if route.request.method == "POST":
            sent.append(parse_qs(route.request.post_data or ""))
            route.continue_()
            return
        loads.append(route.request.url)
        if not renew or len(loads) == 1:
            route.continue_()
            return
        fetched = route.fetch()
        body = re.sub(
            r'(name="request_key" value=")[^"]+"', rf'\g<1>{uuid4()}"', fetched.text()
        )
        route.fulfill(response=fetched, body=body)

    page.route(lambda url: url.split("#")[0] == origin + address, handle)
    return sent


@pytest.mark.parametrize("width", [320, 1280])
def test_the_primary_becomes_save_and_continue_in_the_same_place(
    page, component_origin, axe_source, width
):
    """A change turns Continue into Save and continue; nothing in the row moves.

    Both the shared cell and the visible button keep their place and size,
    on a phone and on a wide screen: the two are drawn equally tall.
    """
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + DIRECT)
    proceed, save, cell = primary(page)
    visible(proceed)
    hidden(save)
    assert proceed.inner_text() == "Continue to Pages and emails"
    # The page draws no Save of its own and no review dialog's opener.
    assert page.locator("#settings-form button", has_text="Save").count() == 0
    assert page.locator("form[data-review-open]").count() == 0

    def placed(button):
        """The cell's and the visible ``button``'s boxes within the row.

        All three boxes are read in one script, so they come from one
        layout. Firefox scrolls the filled field into view and can settle
        that scroll a pixel later (scrollY 38, then 37); separate
        ``bounding_box()`` calls straddling it read the unmoved row and cell
        a whole pixel apart, as if the cell had moved within the row.
        """
        return button.evaluate(
            """(button, cell) => {
              const row = cell
                .closest("[data-campaign-step-actions]")
                .getBoundingClientRect();
              return [cell, button].flatMap((node) => {
                const box = node.getBoundingClientRect();
                return [box.x - row.x, box.y - row.y, box.width, box.height];
              });
            }""",
            cell.element_handle(),
        )

    before = placed(proceed)
    assert same_place(before[:4], before[4:])
    field = page.locator("#id_options-0-label")
    saved = field.input_value()
    field.fill("Renamed option")
    available(save)
    hidden(proceed)
    assert save.inner_text() == "Save and continue"
    assert same_place(placed(save), before)
    assert axe_violations(page, axe_source) == []
    # Undoing the change gives Continue back.
    field.fill(saved)
    available(proceed)
    hidden(save)
    assert same_place(placed(proceed), before)
    assert failures == []


def test_save_and_continue_saves_and_opens_the_next_step(page, component_origin):
    """The save posts ``save`` with the page's key, then the next step opens."""
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    sent = []

    def watch(route):
        """Note each save's body and let it through to the fixture server."""
        if route.request.method == "POST":
            sent.append(route.request.post_data or "")
        route.continue_()

    page.route(component_origin + DIRECT, watch)
    page.goto(component_origin + DIRECT)
    _, save, _ = primary(page)
    page.locator("#id_options-0-label").fill("Renamed option")
    save.click()
    page.wait_for_url(component_origin + CONTENT)
    assert len(sent) == 1
    assert "action=save" in sent[0] and "request_key=" in sent[0]
    assert "action=preview" not in sent[0]
    line = page.locator("#campaign-step-outcome")
    visible(line)
    assert line.inner_text().startswith("Share options saved. Applied at ")
    assert failures == []


def test_a_refused_save_stays_on_the_page_with_its_reason(page, component_origin):
    """A refusal is said in the row's status line; the form keeps the change."""
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    refusal = {
        "errors": [{"code": "stale_version", "field_id": None, "message": ""}],
        "refusal": {
            "message": "This page changed in another tab or session.",
            "fix": "Reload the page to see the latest version, then make your "
            "change again.",
            "link": None,
        },
    }

    def refuse(route):
        """Refuse the save as a stale page; let the page itself load."""
        if route.request.method != "POST":
            route.continue_()
            return
        route.fulfill(
            status=409, content_type="application/json", body=json.dumps(refusal)
        )

    page.route(component_origin + DIRECT, refuse)
    page.goto(component_origin + DIRECT)
    _, save, _ = primary(page)
    field = page.locator("#id_options-0-label")
    field.fill("Renamed option")
    save.click()
    has_text(
        page.locator("#campaign-step-status"),
        "This page changed in another tab or session. Reload the page to see "
        "the latest version, then make your change again.",
    )
    assert page.url == component_origin + DIRECT
    assert field.input_value() == "Renamed option"
    available(save)
    assert failures == []


def test_a_save_not_applied_keeps_the_typed_values_with_a_new_key(
    page, component_origin
):
    """A save the installer did not apply keeps the page and what was typed.

    The first change's request key is spent: only a new key is taken from
    the page, so a retry with the same values is a request of its own and
    is never answered with the first.
    """
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    sent = watch_posts(page, DIRECT_RETRY, component_origin, renew=True)
    page.goto(component_origin + DIRECT_RETRY)
    _, save, _ = primary(page)
    field = page.locator("#id_options-0-label")
    field.fill("First change")
    save.click()
    line = page.locator("#campaign-step-status")
    has_text(line, re.compile(r"not applied"))
    visible(line.get_by_role("link", name="See the change's status"))
    assert page.url == component_origin + DIRECT_RETRY
    # The typed value survives, and Save and continue stays available.
    assert field.input_value() == "First change"
    available(save)
    save.click()
    has_text(line, re.compile(r"not applied"))
    assert [body["options-0-label"] for body in sent] == [
        ["First change"],
        ["First change"],
    ]
    first, second = (body["request_key"] for body in sent)
    assert first != second
    assert failures == []


def test_a_spent_page_that_cannot_be_read_again_holds_save(page, component_origin):
    """When the page cannot be drawn again, Save and continue is held."""
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    loads = []

    def signed_out(route):
        """The first load draws the page; later ones are refused."""
        if route.request.method == "POST":
            route.continue_()
            return
        loads.append(route.request.url)
        if len(loads) == 1:
            route.continue_()
        else:
            route.fulfill(status=403, content_type="text/html", body="<p>No</p>")

    page.route(
        lambda url: url.split("#")[0] == component_origin + DIRECT_RETRY, signed_out
    )
    page.goto(component_origin + DIRECT_RETRY)
    _, save, _ = primary(page)
    page.locator("#id_options-0-label").fill("First change")
    save.click()
    has_text(
        page.locator("#campaign-step-status"),
        re.compile(r"not applied.*Reload the page to make another change\.", re.S),
    )
    from playwright.sync_api import expect

    expect(save).to_be_disabled()
    page.locator("#id_options-0-label").fill("Another change")
    expect(save).to_be_disabled()
    assert failures == []


def test_campaign_settings_saves_directly_and_says_what_was_removed(
    page, component_origin
):
    """Campaign settings' Save and continue saves, then the next step opens.

    Turning Financial stewardship off removes the draft's share options, so
    the next step's status line says so too.
    """
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    sent = watch_posts(page, DIRECT_SETTINGS, component_origin)
    page.goto(component_origin + DIRECT_SETTINGS)
    proceed, save, _ = primary(page)
    visible(proceed)
    hidden(save)
    assert page.locator("#settings-form button", has_text="Save").count() == 0
    page.locator("#id_financial_enabled").uncheck()
    available(save)
    save.click()
    page.wait_for_url(component_origin + CONTENT)
    [body] = sent
    assert body["action"] == ["save"] and "request_key" in body
    assert "financial_enabled" not in body
    line = page.locator("#campaign-step-outcome")
    visible(line)
    assert re.fullmatch(
        r"Campaign settings saved\. Applied at \d{1,2}:\d{2}\s?[AP]M\. "
        r"Its share options were removed\.",
        line.inner_text().strip(),
    )
    assert failures == []


def test_a_value_refused_at_its_field_is_marked_there(page, component_origin):
    """A direct save refused for a value marks the field and the summary.

    The page stays, the form keeps what was typed, and nothing opens.
    """
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))

    def refuse(route):
        """Answer the save with the page drawn again with the name's error."""
        if route.request.method != "POST":
            route.continue_()
            return
        fetched = route.fetch(
            url=component_origin + DIRECT_SETTINGS_REFUSED, method="GET"
        )
        route.fulfill(response=fetched, status=400)

    page.route(
        lambda url: url.split("#")[0] == component_origin + DIRECT_SETTINGS, refuse
    )
    page.goto(component_origin + DIRECT_SETTINGS)
    _, save, _ = primary(page)
    name = page.get_by_label("Campaign name")
    name.fill("Renamed")
    available(save)
    save.click()
    visible(page.locator("[data-error-summary]"))
    has_attribute(name, "aria-invalid", "true")
    assert page.url == component_origin + DIRECT_SETTINGS
    assert name.input_value() == "Renamed"
    assert page.locator("dialog[open]").count() == 0
    assert failures == []
