"""Admin empty tables and lists say why they are empty and what to do (#227).

The text guard (tests/stewardship/test_admin_empty_states.py) checks every
empty message in the templates; this checks the converted pages in a real
browser: the message is drawn where the rows would be, with the page's help
still closed, no script error, and nothing moving once the page has loaded.
"""

import pytest

from .waits import has_text, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

HISTORY_SAVE = "Each follow-up you save, with Save follow-up or Save and next"
# Fixture page, and the start of each message its empty tables or lists show.
EMPTY_PAGES = {
    "/census-changes-none": (
        "No census changes need your work right now. Changes that do are",
    ),
    "/census-changes-none-all": (
        "No census changes yet. A change is listed here when a Family's live "
        "response reports one; to see superseded",
    ),
    "/census-changes-none-everything": (
        "No census changes yet. A change is listed here when a Family's live "
        "response reports one.",
    ),
    "/census-changes-none-searched": (
        "No census changes match these filters. Clear the Search name",
    ),
    "/delivery-no-evidence": ("No Admin evidence yet. Each note or decision",),
    "/go-live-families-none": (
        "No Families have Testing responses left to delete, so cleanup has",
    ),
    "/information-item-new": ("No Staff edits yet. " + HISTORY_SAVE,),
    "/information-item-past-end": (
        "No Staff edits on this page of the history. Use Newer history",
    ),
    "/followup-item-new": ("No follow-up edits yet. " + HISTORY_SAVE,),
    "/followup-item-past-end": (
        "No follow-up edits on this page of the history. Use Newer history",
    ),
    "/limitations-report-none": (
        "No Member has said they cannot participate",
        "No Family has said it cannot attend Mass",
    ),
    "/limitations-report-none-searched": (
        "No Members match these filters. Clear the Search name",
        "No Families match these filters. Clear the Search name",
    ),
    "/roster-none": ("No roster changes to enter. A change is listed here",),
    "/roster-none-ministry": (
        "No roster changes to enter for this Ministry. Set Ministry to",
    ),
    "/roster-none-all": ("No roster changes yet. A change is listed here",),
    "/roster-none-all-ministry": (
        "No roster changes at this stage for this Ministry. Set Ministry to",
    ),
    "/source-form-none": ("Every Family that can use the portal can open the form,",),
}
# The Filters box beside each empty table with one (#227): its title, from
# the same count the message is chosen by, so the two can't disagree. Census
# changes' box is always open (#1682).
FILTER_PANELS = {
    "/census-changes-none": "Filters: none applied",
    "/census-changes-none-all": "Filters: 1 applied",
    "/census-changes-none-everything": "Filters: 2 applied",
    "/census-changes-none-searched": "Filters: 1 applied",
}


@pytest.mark.parametrize("path", sorted(EMPTY_PAGES))
def test_empty_state_says_why_and_stays_put(page, component_origin, path):
    """Each empty message shows with help closed, no page error and no shift."""
    from playwright.sync_api import expect

    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.goto(component_origin + path)
    page.evaluate("document.fonts.ready.then(() => true)")
    panel = page.locator("details[data-about-page]")
    expect(panel).to_have_count(1)
    expect(panel).not_to_have_attribute("open", "")
    messages = [page.locator("main").get_by_text(text) for text in EMPTY_PAGES[path]]
    for message in messages:
        expect(message).to_have_count(1)
        visible(message)
    if path in FILTER_PANELS:
        filters = page.locator("section[data-filter-panel]")
        has_text(filters.get_by_role("heading", level=2), FILTER_PANELS[path])
        visible(filters.get_by_role("button", name="Apply filters"))
    before = [message.bounding_box() for message in messages]
    # Anything the page's scripts draw after load (times, counts, in-place
    # regions) must not move a message under the reader (no layout shift).
    page.wait_for_timeout(500)
    assert [message.bounding_box() for message in messages] == before
    assert not failures, failures
