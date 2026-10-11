"""The setup wizard's access step lists users by address only (#993)."""

import pytest

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)


def test_access_step_offers_three_address_lists_and_no_domain(page, component_origin):
    """Administrators, Staff and Ministry leaders by address; no hosted domain.

    Sign-in is by address only (#952), so each listed address becomes one
    user and the page never offers to let in a whole domain.
    """
    page.goto(component_origin + "/setup-access")
    fields = page.locator("form textarea").evaluate_all(
        "fields => fields.map(field => field.name)"
    )
    assert sorted(fields) == [
        "admin_addresses",
        "ministry_addresses",
        "staff_addresses",
    ]
    # The page's about text sits in a collapsed help panel.
    text = page.locator("main").text_content()
    assert "Each address you list becomes one user" in text
    assert "never to a whole domain" in text
    assert "Domains may grant" not in text
