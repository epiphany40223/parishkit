"""The Users page's Email Ministry leaders section in real browsers (#1592).

At the foot of the Users page: a selection column, Name, Email, Last
successful sign-in and Last emailed. The bulk bar is the shared one
(table-selection.html): ticking a row enables Email selected without moving
anything, and with nobody to email both buttons are unavailable with the
reason. Email selected posts only the ticked addresses, and the answer, the
Email Ministry leaders review, opens in the shared review dialog with each
leader's Ministries and open requests, who is skipped, and, in Testing,
where the emails go instead. Apply posts the signed review, then the page's
regions are redrawn in place (no reload) and Last emailed says the email is
queued, with the section's status line saying so. With no current campaign
with Ministries, the section says so in place of the table. Pages and POST
answers are user_components'.
"""

from urllib.parse import parse_qs

import pytest

from .test_components import axe_violations
from .user_components import (
    EMAILS_UNAVAILABLE,
    LIST,
    LIST_EMAILS,
    NO_CAMPAIGN,
    SINGLE,
)
from .waits import eventually, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)
REASON = "#emails-reason"


def region(page):
    """The Email Ministry leaders table's region."""
    return page.locator("#emails-table")


def boxes(page):
    """Where the section's bulk bar buttons and first row sit."""
    return page.evaluate(
        """() => [
          ...document.querySelectorAll('#emails-table .bulk-bar button:not([hidden])'),
          document.querySelector('#emails-table tbody tr'),
        ].map(node => {
          const box = node.getBoundingClientRect();
          const x = box.x + window.scrollX, y = box.y + window.scrollY;
          return [Math.round(x), Math.round(y)];
        })"""
    )


def watch(page):
    """Fail on any script error, and mark the page so a reload shows."""
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.evaluate("window.notReloaded = true")
    return failures


@pytest.mark.parametrize("width", [320, 1280])
def test_the_section_and_its_review_are_accessible(
    page, component_origin, axe_source, width
):
    """No sideways scroll and no axe violation, with the review open too."""
    page.set_viewport_size({"width": width, "height": 900})
    for path in (LIST, SINGLE, EMAILS_UNAVAILABLE):
        page.goto(component_origin + path)
        assert page.evaluate(
            "document.documentElement.scrollWidth <= window.innerWidth"
        ), path
        assert axe_violations(page, axe_source) == [], path
    page.goto(component_origin + LIST)
    region(page).get_by_role(
        "checkbox", name="Select Leader, Lee (leader@example.org)"
    ).check()
    page.get_by_role("button", name="Email selected").click()
    visible(page.get_by_role("dialog", name="Review Ministry leader emails"))
    assert axe_violations(page, axe_source) == []


def test_columns_follow_the_shared_order(page, component_origin):
    """Selection, Name, Email, Last successful sign-in, Last emailed."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + LIST)
    headings = [
        text.strip().split("\n")[0].strip()
        for text in region(page).locator("thead th").all_inner_texts()
    ]
    assert headings[1:] == [
        "Name",
        "Email",
        "Last successful sign-in",
        "Last emailed",
    ]
    visible(
        region(page).get_by_role(
            "checkbox", name="Select every Ministry leader on this page"
        )
    )
    cells = region(page).locator("tbody tr")
    assert cells.nth(0).locator("td").nth(3).inner_text().strip() == "Never"
    emailed = cells.nth(1).locator("td").nth(3)
    assert emailed.inner_text().strip().endswith("· Delivered")
    # The time is drawn in the browser's zone, compact, as the users table's.
    assert emailed.locator("time[data-local-instant][data-compact]").count() == 1


def test_ticking_a_row_enables_email_selected_without_moving_anything(
    page, component_origin
):
    """The reason is visible text in a reserved line; ticking clears it."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + LIST)
    selected = page.get_by_role("button", name="Email selected")
    reason = page.locator(REASON)
    eventually(page, f"document.querySelector('{REASON}').textContent.trim() !== ''")
    assert selected.is_disabled()
    assert reason.inner_text() == "Select at least one leader to use Email selected."
    before = boxes(page)
    region(page).get_by_role(
        "checkbox", name="Select Leader, Lee (leader@example.org)"
    ).check()
    eventually(
        page, "!document.querySelector('#emails-table [data-bulk-action]').disabled"
    )
    assert not reason.is_visible()
    assert boxes(page) == before
    visible(region(page).get_by_text("1 selected"))


def test_nobody_to_email_says_why(page, component_origin):
    """With no Ministry leader user, Email selected is unavailable, with why."""
    page.goto(component_origin + SINGLE)
    assert page.get_by_role("button", name="Email selected").is_disabled()
    eventually(
        page,
        f"document.querySelector('{REASON}').textContent.includes('Nobody to email')",
    )
    visible(
        region(page).get_by_role("cell", name="No user has the role Ministry leader")
    )


def test_no_campaign_with_ministries_replaces_the_table(page, component_origin):
    """The section explains itself and offers nothing to apply."""
    page.goto(component_origin + EMAILS_UNAVAILABLE)
    visible(page.locator("#emails-unavailable").get_by_text(str(NO_CAMPAIGN)))
    assert page.get_by_role("button", name="Email selected").count() == 0
    assert page.get_by_role("button", name="Email all").count() == 0


def test_email_selected_reviews_then_applies_in_place(page, component_origin):
    """The review lists each leader's Ministries; Apply records and redraws."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + LIST_EMAILS)
    failures = watch(page)
    region(page).get_by_role(
        "checkbox", name="Select Young, Cy (cy.young@example.org)"
    ).check()
    with page.expect_request(lambda request: request.method == "POST") as sent:
        page.get_by_role("button", name="Email selected").click()
    fields = parse_qs(sent.value.post_data or "")
    assert fields["email"] == ["cy.young@example.org"]
    assert set(fields) == {"csrfmiddlewaretoken", "email"}
    dialog = page.get_by_role("dialog", name="Review Ministry leader emails")
    visible(dialog)
    assert page.evaluate("document.activeElement.id") == "emails-review-title"
    visible(dialog.get_by_role("heading", name="2 emails"))
    visible(dialog.get_by_text("Choir: 2 asking to join · 1 asking to leave"))
    visible(dialog.get_by_text("Food pantry: No open requests"))
    visible(
        dialog.get_by_text(
            "Testing mode: every email goes to the Testing recipient, "
            "test@example.org, instead of the leader."
        )
    )
    visible(dialog.get_by_role("heading", name="Skipped"))
    with page.expect_request(lambda request: request.method == "POST") as applied:
        dialog.get_by_role("button", name="Apply").click()
    fields = parse_qs(applied.value.post_data or "")
    assert fields["action"] == ["confirm"]
    assert fields["preview"] == ["synthetic-preview"]
    eventually(page, "!document.getElementById('emails-review').open")
    visible(page.locator("#emails-outcome").get_by_text("Emails queued."))
    visible(region(page).get_by_text("· Still sending (queued)"))
    assert page.evaluate("window.notReloaded === true")
    assert not failures
