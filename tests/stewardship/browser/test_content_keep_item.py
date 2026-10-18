"""Pages and emails' editor's Keep as is and go back (#1404 PR 6b).

The editor as the campaign wizard draws it on a listed item
(``stepper_components``' ``EDITOR_KEEP``): while its form holds no change
the action row's primary is Keep as is and go back, and once the form holds
a change Save and go back takes the same place, so nothing moves. Keep as
is posts the step and the item without a page load and opens Pages and
emails (``CONTENT``) with "<label> kept as is." in its status line and focus
on the item's row; Save and go back returns there with focus on the row
too. A refused mark is said in the row's status line and the editor stays.
"""

import json
import re
from urllib.parse import parse_qs

import pytest
from django.urls import reverse

from .stepper_components import CONTENT, EDITOR_KEEP
from .test_campaign_subpage_direct_save import placed, type_in_editor
from .test_components import axe_violations
from .waits import has_text, hidden, same_place, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)
CONTINUE = reverse("admin:campaign_step_continue")


def primary(page):
    """The action row's Keep as is and go back and its Save and go back."""
    actions = page.locator("[data-campaign-step-actions]")
    return actions.get_by_role(
        "button", name="Keep as is and go back"
    ), actions.locator("[data-campaign-save]")


def focused(locator):
    """Assert that ``locator`` comes to have the focus, waiting."""
    from playwright.sync_api import expect

    expect(locator).to_be_focused()


def test_keep_as_is_goes_back_with_the_row_focused(page, component_origin, axe_source):
    """Keep as is and go back marks the item and opens Pages and emails."""
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    sent = []

    def note(route):
        """Note the mark's posted values and let it through."""
        sent.append(parse_qs(route.request.post_data or ""))
        route.continue_()

    page.route(lambda url: url.split("#")[0] == component_origin + CONTINUE, note)
    page.goto(component_origin + EDITOR_KEEP)
    keep, save = primary(page)
    visible(keep)
    # Save and go back is held, out of sight, until the form holds a change.
    hidden(save)
    assert axe_violations(page, axe_source) == []
    keep.click()
    page.wait_for_url(component_origin + CONTENT)
    assert [
        {key: value for key, value in body.items() if key != "csrfmiddlewaretoken"}
        for body in sent
    ] == [{"step": ["content"], "item": ["page:welcome"]}]
    has_text(page.locator("#campaign-step-outcome"), "Welcome page kept as is.")
    row = page.locator("#content-page-welcome")
    focused(row)
    assert "Kept as is" in row.inner_text()
    assert failures == []


@pytest.mark.parametrize("width", [320, 1280])
def test_keep_and_save_share_one_place(page, component_origin, width):
    """A change swaps Keep as is for Save and go back in the same place, and
    undoing it swaps them back; neither the row nor the swapped control
    moves. At 320 the page does not scroll sideways."""
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + EDITOR_KEEP)
    keep, save = primary(page)
    back = page.locator("[data-campaign-step-actions] [data-campaign-step-back]")
    visible(keep)
    before = placed(page, back, keep)
    assert page.evaluate(
        "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
    )
    type_in_editor(page, " and thanks")
    visible(save)
    hidden(keep)
    # Save and go back sits where Keep as is was: same line, same left edge.
    after = placed(page, back, save)
    assert same_place(after[0], before[0], tolerance=1)
    assert same_place(after[1]["y"], before[1]["y"], tolerance=1)
    assert same_place(after[1]["x"], before[1]["x"], tolerance=1)
    for _ in " and thanks":
        page.keyboard.press("Backspace")
    visible(keep)
    hidden(save)
    assert same_place(placed(page, back, keep), before, tolerance=1)
    assert failures == []


def test_save_and_go_back_focuses_the_row(page, component_origin):
    """Save and go back returns to Pages and emails with focus on the row."""
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.goto(component_origin + EDITOR_KEEP)
    _, save = primary(page)
    type_in_editor(page, " and thanks")
    visible(save)
    save.click()
    page.wait_for_url(component_origin + CONTENT)
    line = page.locator("#campaign-step-outcome")
    visible(line)
    assert re.fullmatch(
        r"Welcome page saved\. Applied at \d{1,2}:\d{2}\s?[AP]M\.",
        line.inner_text().strip(),
    )
    focused(page.locator("#content-page-welcome"))
    assert failures == []


def test_a_refused_keep_says_why_and_stays(page, component_origin):
    """A mark refused (the item changed meanwhile) is said in the status
    line, and the editor stays with Keep as is available again."""
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    refusal = {
        "message": "That page or email has changed since this page was opened, "
        "so it was not marked.",
        "fix": "Return to Pages and emails, then open it again.",
    }

    def refuse(route):
        """Answer the mark as a stale item is answered (409, JSON)."""
        route.fulfill(
            status=409,
            content_type="application/json",
            body=json.dumps({"refusal": refusal}),
        )

    page.route(lambda url: url.split("#")[0] == component_origin + CONTINUE, refuse)
    page.goto(component_origin + EDITOR_KEEP)
    keep, _ = primary(page)
    keep.click()
    has_text(
        page.locator("#campaign-step-status"),
        f"{refusal['message']} {refusal['fix']}",
    )
    assert page.url == component_origin + EDITOR_KEEP
    visible(keep)
    assert keep.get_attribute("aria-disabled") is None
    assert failures == []
