"""A Ministry request the parish added is undone and dropped like any other (#2173).

Undoing its decision (choosing the pressed Accept again) sends it back to
To review, where it stays listed with the parish's value, and Ignore drops
it: it is Ignored, keeps that value, and choosing Ignore again undoes it.
The fixture server answers each decision's POST with the page after it
(``followup_components``: ``ADDED_ACCEPTED``, ``ADDED_TO_REVIEW``,
``ADDED_IGNORED``).
"""

import pytest
from django.http import QueryDict

from .followup_components import (
    ADDED_ACCEPTED,
    ADDED_IGNORED,
    ADDED_REQUEST,
    ADDED_TO_REVIEW,
)
from .test_ministry_followup import icon_named, worklist_row
from .test_table_sorting import fulfil_post_with
from .waits import has_text, hidden

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)
REVIEWER_VALUE = "Join Another Ministry"


def reviewer_cell(page):
    """The added row's Reviewer's value cell, found by its column heading."""
    headings = [
        node.inner_text().strip() for node in page.locator("#table thead th").all()
    ]
    cells = worklist_row(page, ADDED_REQUEST).locator(":scope > th, :scope > td")
    return cells.nth(headings.index("Reviewer's value"))


def decide(page, origin, label, answer, sent):
    """Choose the added row's ``label`` icon and Record, answered by ``answer``."""
    page.unroute("**/reports/ministries/")

    def choose(request):
        """Keep the body; answer with the page after the decision."""
        sent.append(QueryDict(request.post_data))
        return answer

    fulfil_post_with(page, "**/reports/ministries/", origin, choose)
    icon_named(page, ADDED_REQUEST, label).click()
    dialog = page.locator("#requests-decide-dialog")
    body = dialog.locator("[data-confirm-body]").inner_text()
    dialog.get_by_role("button", name="Record").click()
    hidden(dialog)
    return body


def stage(page):
    """The added row's Stage chip."""
    return worklist_row(page, ADDED_REQUEST).locator(".stage-chip")


def test_undoing_an_added_request_returns_it_to_to_review(page, component_origin):
    """The pressed Accept of an added request sends it back to To review
    (never withdrawn): the row stays, offers Accept, Edit and Ignore, and
    still shows the parish's value; in place."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + ADDED_ACCEPTED)
    page.evaluate("window.notReloaded = true")
    pressed = icon_named(page, ADDED_REQUEST, "Accept")
    assert pressed.get_attribute("aria-pressed") == "true"
    sent = []
    body = decide(page, component_origin, "Send", ADDED_TO_REVIEW, sent)
    assert "goes back to To review" in body
    assert sent[0]["decide"] == "back_to_review"
    assert sent[0].getlist("item") == [str(ADDED_REQUEST)]
    has_text(stage(page), "To review")
    for label in ("Accept", "Edit", "Ignore"):
        assert icon_named(page, ADDED_REQUEST, label).get_attribute("aria-pressed") in (
            "false",
            None,
        )
    assert reviewer_cell(page).inner_text().strip() == REVIEWER_VALUE
    assert page.evaluate("window.notReloaded") is True


def test_ignore_drops_an_added_request_reversibly(page, component_origin):
    """Ignore on an added request To review makes it Ignored with the
    parish's value kept; choosing Ignore again sends it back to To review."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + ADDED_TO_REVIEW)
    page.evaluate("window.notReloaded = true")
    sent = []
    decide(page, component_origin, "Ignore", ADDED_IGNORED, sent)
    assert sent[0]["decide"] == "ignore"
    has_text(stage(page), "Ignored")
    ignore = icon_named(page, ADDED_REQUEST, "Ignore")
    assert ignore.get_attribute("aria-pressed") == "true"
    assert reviewer_cell(page).inner_text().strip() == REVIEWER_VALUE
    body = decide(page, component_origin, "Send", ADDED_TO_REVIEW, sent)
    assert "goes back to To review" in body
    assert sent[1]["decide"] == "back_to_review"
    has_text(stage(page), "To review")
    assert reviewer_cell(page).inner_text().strip() == REVIEWER_VALUE
    assert page.evaluate("window.notReloaded") is True
