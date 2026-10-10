"""Outgoing mail and Refused addresses lead to each other (#935).

Outgoing mail marks each email with an unresolved refused address and counts
them in its link line; Refused addresses names each Family beside its own
Family DUID column and links the email that recorded the refusal; and a
refused address's page links both and clears it in place. The page is
served at its real address, and the fixture server answers its clearance
with a real Post/Redirect/Get redirect (delivery_components.POSTS).
"""

import pytest

from .delivery_components import CLEAR, REFUSAL
from .test_in_place import MARK, MARKED, VIEWPORT, count_requests, scroll_below
from .waits import has_text

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

CLEAR_BUTTON = "#refusal-clearance button[type=submit]"


def test_outgoing_mail_marks_emails_with_refused_addresses(page, component_origin):
    """Each mark links that Family's refused addresses; the link line counts
    every unresolved one. Both are drawn with the page, so nothing appears
    or disappears under the pointer."""
    page.goto(component_origin + "/deliveries-names")
    rows = page.locator("[data-table-region] tbody tr")
    first = rows.nth(0).get_by_role("link", name="2 addresses refused")
    assert first.get_attribute("href") == "/admin/mail/refusals/?duid=12345"
    second = rows.nth(1).get_by_role("link", name="1 address refused")
    assert second.get_attribute("href") == "/admin/mail/refusals/?duid=4021"
    # The Administrator report has no refused address.
    assert "refused" not in rows.nth(2).inner_text()
    # The mark is a second line of the State cell, under the state.
    state = rows.nth(0).locator("td").nth(4)
    assert state.inner_text().split("\n") == [
        "Not sure it arrived",
        "2 addresses refused",
    ]
    link = page.get_by_role("link", name="Review refused addresses (3 unresolved)")
    assert link.get_attribute("href") == "/admin/mail/refusals/"
    assert link.evaluate(
        "node => !node.closest('[data-table-region], [data-in-place-region]')"
    )


def test_without_refusals_the_link_line_is_unchanged(page, component_origin):
    """No count and no mark while nothing is refused."""
    page.goto(component_origin + "/deliveries")
    assert page.get_by_role("link", name="Review refused addresses", exact=True).count()
    assert "refused" not in page.locator("[data-table-region] tbody").inner_text()


def test_refused_addresses_name_the_family_and_link_the_email(page, component_origin):
    """Dates first, then the Family's name and its own DUID column (#932); the
    address opens the refusal and Open email the email that recorded it."""
    from playwright.sync_api import expect

    page.goto(component_origin + "/delivery-refusals")
    table = page.locator("table.data-table")
    headings = table.locator("thead th").all_inner_texts()
    assert [text.split("\n")[0].strip() for text in headings] == [
        "Refused at",
        "Family",
        "Family DUID",
        "Address",
        "Email",
    ]
    # Email is a plain heading; the others, Family included, sort on the
    # server (a table with a Family name and DUID sorts on both, #932).
    sortable = [
        table.locator("thead th").nth(column).locator(".sort-link").count()
        for column in range(5)
    ]
    assert sortable == [1, 1, 1, 1, 0]
    row = table.locator("tbody tr").first
    assert row.locator("th").inner_text() == "Castellanos, Maximiliana and Bartholomew"
    assert row.locator("td").nth(1).inner_text() == "12345"
    address = row.get_by_role("link", name="head@example.org")
    assert address.get_attribute("href") == REFUSAL
    email = row.get_by_role("link", name="Open email")
    assert email.get_attribute("href").startswith("/admin/mail/outgoing/")
    # Browser-local time: noon UTC in the fixture browser's Los Angeles.
    expect(row.locator("time").first).to_contain_text("5:00 AM")
    back = page.get_by_role("link", name="See all outgoing mail")
    assert back.get_attribute("href") == "/admin/mail/outgoing/"


def test_a_refused_address_names_its_family_and_links_the_email(page, component_origin):
    """The refusal's page names the Family beside its DUID and links that
    Family's refused addresses and the email that recorded the refusal."""
    page.goto(component_origin + REFUSAL)
    assert "Castellanos, Maximiliana and Bartholomew" in page.inner_text("main")
    family = page.get_by_role("link", name="See this Family's refused addresses")
    assert family.get_attribute("href") == "/admin/mail/refusals/?duid=12345"
    email = page.get_by_role("link", name="Open the email that recorded this refusal")
    assert email.get_attribute("href").startswith("/admin/mail/outgoing/")


def test_clearing_waits_for_the_note_and_the_confirmation(page, component_origin):
    """Clear verified refusal is unavailable until both are filled in."""
    page.goto(component_origin + REFUSAL)
    button = page.locator(CLEAR_BUTTON)
    assert button.is_disabled()
    page.get_by_label("How did you verify that this mailbox now works?").fill("   ")
    page.get_by_role("checkbox").check()
    assert button.is_disabled()
    page.get_by_label("How did you verify that this mailbox now works?").fill(
        "Called the Family; the mailbox works again."
    )
    assert button.is_enabled()
    page.get_by_role("checkbox").uncheck()
    assert button.is_disabled()


def test_clearing_happens_in_place(page, component_origin):
    """One POST, no reload, the reader's place kept, focus on the result."""
    from playwright.sync_api import expect

    page.set_viewport_size(VIEWPORT)
    page.goto(component_origin + REFUSAL)
    page.evaluate(MARK)
    posts = count_requests(page, "POST", CLEAR)
    page.get_by_label("How did you verify that this mailbox now works?").fill(
        "Called the Family; the mailbox works again."
    )
    page.get_by_role("checkbox").check()
    offset = scroll_below(page, CLEAR_BUTTON)
    page.locator(CLEAR_BUTTON).click()
    heading = page.get_by_role("heading", name="This refusal has been resolved")
    expect(heading).to_be_focused()
    assert page.evaluate(MARKED) == "kept"
    # No jump to the top: the result is shown where the form was. (The
    # page is shorter now, so the browser may scroll up only as far as
    # the new end of the page requires.)
    assert 0 < page.evaluate("window.scrollY") <= offset
    assert heading.evaluate(
        "node => { const box = node.getBoundingClientRect();"
        " return box.top >= 0 && box.bottom <= innerHeight; }"
    )
    assert len(posts) == 1
    assert "Verified by an Administrator" in page.inner_text("#refusal-clearance")
    has_text(
        page.get_by_role("status").filter(has_text="Refusal cleared."),
        "Refusal cleared.",
    )
