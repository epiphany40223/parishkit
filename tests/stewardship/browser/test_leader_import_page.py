"""The Users page's ParishSoft Ministry leaders table in real browsers (#952 slice 6).

Below the users table: a selection column, Member, Member DUID, Email,
Ministries led (a short list, the whole list in its tooltip) and Role. The
bulk bar is the shared one (table-selection.html, #879), its reason visible
text in a reserved line (Administrator decision B2): ticking a row enables
Import selected and hides the reason without moving anything. With nobody
to import, both buttons are unavailable and the same line gives the
empty-table reason. Import selected posts only the
ticked addresses, and the answer, the Import Ministry leaders review, opens
in the shared review dialog (#750; its Apply is in test_review_dialog).
Pages and POST answers are user_components'.
"""

from urllib.parse import parse_qs

import pytest

from .test_components import axe_violations
from .user_components import LEADERS, LIST, SINGLE
from .waits import eventually, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)
REASON = "#leaders-import-reason"


def region(page):
    """The leaders table's region."""
    return page.locator("#leaders-table")


def boxes(page):
    """Where the bulk bar's buttons and the table's first row sit."""
    return page.evaluate(
        """() => [
          ...document.querySelectorAll('#leaders-table .bulk-bar button:not([hidden])'),
          document.querySelector('#leaders-table tbody tr'),
        ].map(node => {
          const box = node.getBoundingClientRect();
          const x = box.x + window.scrollX, y = box.y + window.scrollY;
          return [Math.round(x), Math.round(y)];
        })"""
    )


@pytest.mark.parametrize("width", [320, 1280])
def test_the_leaders_table_and_review_are_accessible(
    page, component_origin, axe_source, width
):
    """No sideways page scroll and no axe violation, with or without
    leaders, and with the review open in its dialog."""
    page.set_viewport_size({"width": width, "height": 900})
    for path in (LIST, SINGLE):
        page.goto(component_origin + path)
        assert page.evaluate(
            "document.documentElement.scrollWidth <= window.innerWidth"
        ), path
        assert axe_violations(page, axe_source) == [], path
    page.goto(component_origin + LIST)
    page.get_by_role("button", name="Import all").click()
    visible(page.get_by_role("dialog", name="Review Ministry leader import"))
    assert axe_violations(page, axe_source) == []


def test_columns_follow_the_shared_order(page, component_origin):
    """Selection, Member, Member DUID, Email, Ministries led, Role; Member sorted."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + LIST)
    # A sortable heading's text may carry its direction arrow after the name.
    headings = [
        text.strip().split("\n")[0].strip()
        for text in region(page).locator("thead th").all_inner_texts()
    ]
    assert headings[1:] == ["Member", "Member DUID", "Email", "Ministries led", "Role"]
    visible(
        region(page).get_by_role("checkbox", name="Select every leader on this page")
    )
    assert (
        region(page).locator('th[data-sort-column="name"]').get_attribute("aria-sort")
        == "ascending"
    )
    names = [
        text.strip()
        for text in region(page).locator("tbody th[scope=row]").all_inner_texts()
    ]
    assert names == [row["name"] for row in LEADERS]
    # Each Member's DUID in its own column right after the name (#932).
    assert region(page).locator("tbody td.numeric").all_inner_texts() == [
        str(row["member_duid"]) for row in LEADERS
    ]
    # A long Ministry list is cut short, whole in its tooltip.
    many = region(page).locator(".cell-ellipsis[title^='Choir, Food pantry']")
    assert many.inner_text() == "Choir, Food pantry and 3 more"
    assert many.get_attribute("title") == (
        "Choir, Food pantry, Lectors, Ushers, Youth ministry"
    )
    visible(page.get_by_text("2 ParishSoft Ministry leaders have no email address"))


def test_ticking_a_row_enables_import_selected_without_moving_anything(
    page, component_origin
):
    """The reason is visible text in a reserved line; ticking clears it in place."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + LIST)
    selected = page.get_by_role("button", name="Import selected")
    everyone = page.get_by_role("button", name="Import all")
    reason = page.locator(REASON)
    eventually(page, f"document.querySelector('{REASON}').textContent.trim() !== ''")
    assert selected.is_disabled() and everyone.is_enabled()
    assert reason.inner_text() == "Select at least one leader to use Import selected."
    # Visible, not a visually hidden hint, and named by the button.
    assert reason.is_visible() and reason.bounding_box()["width"] > 1
    assert "leaders-import-reason" in selected.get_attribute("aria-describedby")
    assert selected.get_attribute("title") is None
    height = reason.bounding_box()["height"]
    before = boxes(page)
    page.get_by_role(
        "checkbox", name="Select Jones, Ann (ann.jones@example.org)"
    ).check()
    eventually(
        page, "!document.querySelector('#leaders-table [data-bulk-action]').disabled"
    )
    # The shared line (table-selection.html) only hides its text.
    assert not reason.is_visible()
    assert reason.get_attribute("data-cleared") is not None
    assert abs(reason.bounding_box()["height"] - height) <= 1
    assert boxes(page) == before
    assert "leaders-import-reason" not in (
        selected.get_attribute("aria-describedby") or ""
    )
    visible(region(page).get_by_text("1 selected"))
    page.get_by_role(
        "checkbox", name="Select Jones, Ann (ann.jones@example.org)"
    ).uncheck()
    eventually(
        page, "document.querySelector('#leaders-table [data-bulk-action]').disabled"
    )
    assert reason.inner_text() == "Select at least one leader to use Import selected."
    assert boxes(page) == before


def test_nothing_to_import_says_why(page, component_origin):
    """With no rows both buttons are unavailable, the reason shown."""
    page.goto(component_origin + SINGLE)
    assert page.get_by_role("button", name="Import selected").is_disabled()
    everyone = page.get_by_role("button", name="Import all")
    assert everyone.is_disabled()
    assert everyone.get_attribute("aria-describedby") == "leaders-import-reason"
    eventually(
        page,
        f"document.querySelector('{REASON}').textContent.includes('Nothing to import')",
    )
    # Visible text, also named by Import selected, which the script disabled.
    assert page.locator(REASON).is_visible()
    assert "leaders-import-reason" in (
        page.get_by_role("button", name="Import selected").get_attribute(
            "aria-describedby"
        )
        or ""
    )
    visible(page.get_by_text("No ParishSoft Ministry leader is waiting for a user."))


def test_import_selected_posts_only_the_ticked_addresses(page, component_origin):
    """The form posts its scope and ticked addresses; the review answers."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + LIST)
    page.get_by_role(
        "checkbox", name="Select Smith, Bob (bob.smith@example.org)"
    ).check()
    with page.expect_request(lambda request: request.method == "POST") as sent:
        page.get_by_role("button", name="Import selected").click()
    fields = parse_qs(sent.value.post_data or "")
    assert fields["scope"] == ["selected"]
    assert fields["email"] == ["bob.smith@example.org"]
    assert set(fields) == {"csrfmiddlewaretoken", "scope", "email"}
    dialog = page.get_by_role("dialog", name="Review Ministry leader import")
    visible(dialog)
    visible(dialog.get_by_role("heading", name="2 new users"))
    visible(dialog.get_by_role("heading", name="Skipped"))
    visible(dialog.get_by_role("button", name="Apply"))
    # The review shows each new user's Member DUID in its own column right
    # after the Member (Administrator, 2026-10-12, #932).
    added = dialog.locator("table.leaders-table").first
    assert added.locator("thead th").all_inner_texts() == [
        "Member",
        "Member DUID",
        "Email",
        "Role",
    ]
    assert added.locator("tbody td.numeric").all_inner_texts() == [
        str(row["member_duid"]) for row in LEADERS[:2]
    ]


def test_header_checkbox_selects_every_leader(page, component_origin):
    """The shared Select all: every row ticked, the count after the buttons."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + LIST)
    before = boxes(page)
    region(page).get_by_role(
        "checkbox", name="Select every leader on this page"
    ).check()
    visible(region(page).get_by_text(f"{len(LEADERS)} selected"))
    assert page.get_by_role("button", name="Import selected").is_enabled()
    assert boxes(page) == before
