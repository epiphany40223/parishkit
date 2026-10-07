"""History and report pagers act in place (#519 PR 6).

Mail delivery's evidence and attempt pages, the Staff history of an
additional-information item, the follow-up history of a Ministry follow-up
request, and the weekly information report's pages are served at their real
addresses with their next pages (delivery_components, information_components,
followup_components and weekly_components). Each test sets a mark outside
every region, which only a full page load could lose, as test_in_place.py
does.
"""

import pytest

from .followup_components import ITEM as FOLLOWUP_ITEM
from .followup_components import NEWER as FOLLOWUP_NEWER
from .followup_components import OLDER as FOLLOWUP_OLDER
from .followup_components import SAVED as FOLLOWUP_SAVED
from .followup_components import UPDATE as FOLLOWUP_UPDATE
from .information_components import ITEM as INFORMATION_ITEM
from .information_components import NEWER as INFORMATION_NEWER
from .information_components import OLDER as INFORMATION_OLDER
from .test_in_place import MARK, MARKED, VIEWPORT, count_requests, scroll_below
from .waits import has_text, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

TYPED = "Typed <unsaved> notes"


def turn_page(page, origin, region, name, shown, message, focused, address):
    """Activate the pager link ``name`` in ``region`` and check that it acted
    in place: one GET, no reload, the reader's place kept, ``shown`` now in
    the region, focus on the link named ``focused`` (the same link, or the
    other one when this one is gone), ``message`` announced, and the address
    replaced with ``address`` plus the region's fragment."""
    offset = scroll_below(page, f"#{region} nav")
    gets = count_requests(page, "GET", "?page=")
    page.locator(f"#{region}").get_by_role("link", name=name, exact=True).click()
    visible(page.locator(f"#{region}").get_by_text(shown).first)
    assert page.evaluate(MARKED) == "kept"
    assert abs(page.evaluate("window.scrollY") - offset) < 40
    assert page.evaluate(
        "region => document.querySelector('#' + region).contains("
        "document.activeElement) && document.activeElement.isConnected",
        region,
    )
    assert page.evaluate("document.activeElement.textContent") == focused
    has_text(page.get_by_role("status").filter(has_text=message), message)
    assert page.url == origin + address + f"#{region}"
    assert len(gets) == 1


@pytest.mark.parametrize(
    "item, older, newer, notes, region, shown",
    [
        (
            FOLLOWUP_ITEM,
            FOLLOWUP_OLDER,
            FOLLOWUP_NEWER,
            "#followup-item textarea[name=notes]",
            "followup-history",
            "Older <edit>",
        ),
        (
            INFORMATION_ITEM,
            INFORMATION_OLDER,
            INFORMATION_NEWER,
            "#staff-notes",
            "information-history",
            "Older <edit>",
        ),
    ],
)
def test_follow_up_history_pages_in_place(
    page, component_origin, item, older, newer, notes, region, shown
):
    """Older and Newer history page only the history: the Save follow-up
    form above it, and the notes the reader has typed but not saved, stay as
    they are. On the last page Older history is gone, so focus goes to Newer
    history, and back again."""
    page.set_viewport_size(VIEWPORT)
    page.goto(component_origin + item)
    page.evaluate(MARK)
    page.locator(notes).fill(TYPED)
    turn_page(
        page,
        component_origin,
        region,
        "Older history",
        shown,
        "Older history shown.",
        "Newer history",
        older,
    )
    assert page.locator(notes).input_value() == TYPED
    turn_page(
        page,
        component_origin,
        region,
        "Newer history",
        "Version 2"
        if region == "information-history"
        else "Left a <private> voicemail",
        "Newer history shown.",
        "Older history",
        newer,
    )
    assert page.locator(notes).input_value() == TYPED


def test_follow_up_save_after_paging_refreshes_the_history(page, component_origin):
    """A Save made after paging the history still swaps the whole request
    panel, its nested history included, without a reload."""
    page.set_viewport_size(VIEWPORT)
    page.goto(component_origin + FOLLOWUP_ITEM)
    page.evaluate(MARK)
    turn_page(
        page,
        component_origin,
        "followup-history",
        "Older history",
        "Older <edit>",
        "Older history shown.",
        "Newer history",
        FOLLOWUP_OLDER,
    )
    posts = count_requests(page, "POST", "/record/")
    page.locator("#followup-item form[data-in-place] button[type=submit]").click()
    visible(page.locator("#followup-history").get_by_text("Saved <note>", exact=True))
    assert page.evaluate(MARKED) == "kept"
    assert page.locator("#followup-history").get_by_text("Older <edit>").count() == 0
    assert page.locator("#followup-history").count() == 1
    assert page.url == component_origin + FOLLOWUP_SAVED + "#followup-item"
    assert posts == [component_origin + FOLLOWUP_UPDATE]


def test_delivery_history_pages_in_place(page, component_origin):
    """Next page and Previous page on a mail delivery swap its evidence and
    attempt history in place; evidence the reader is typing into a
    resolution form above is kept."""
    page.set_viewport_size(VIEWPORT)
    page.goto(component_origin + "/delivery-paged")
    page.evaluate(MARK)
    evidence = page.locator("#evidence-note")
    evidence.fill(TYPED)
    turn_page(
        page,
        component_origin,
        "delivery-history",
        "Next page",
        "Earlier evidence",
        "Next page shown.",
        "Previous page",
        "/delivery-paged?page=2",
    )
    assert evidence.input_value() == TYPED
    turn_page(
        page,
        component_origin,
        "delivery-history",
        "Previous page",
        "Confirmed <private> evidence",
        "Previous page shown.",
        "Next page",
        "/delivery-paged?page=1",
    )
    assert evidence.input_value() == TYPED


def test_weekly_report_pages_in_place(page, component_origin):
    """Next page and Previous page on a weekly information report swap its
    requests in place and announce the page now shown."""
    page.set_viewport_size(VIEWPORT)
    page.goto(component_origin + "/weekly-digest")
    page.evaluate(MARK)
    turn_page(
        page,
        component_origin,
        "weekly-report-page",
        "Next page",
        "Second Family",
        "Report page shown. Page 2 of 2",
        "Previous page",
        "/weekly-digest?page=2",
    )
    turn_page(
        page,
        component_origin,
        "weekly-report-page",
        "Previous page",
        "Example Family",
        "Report page shown. Page 1 of 2",
        "Next page",
        "/weekly-digest?page=1",
    )
