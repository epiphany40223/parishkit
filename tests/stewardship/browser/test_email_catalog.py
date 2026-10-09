"""Pages and emails lists who sends each email and offers Remove when unused."""

import pytest

from .test_components import axe_violations

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)


@pytest.mark.parametrize("width", [320, 1280])
def test_email_tables_name_their_senders(page, component_origin, axe_source, width):
    """A sent email names its schedules; only the unused one offers Remove."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + "/content-catalog")
    table = page.get_by_role("table", name="Reminder")
    used, spare = table.locator("tbody tr").all()
    assert "Reminder 1, Reminder 2" in used.inner_text()
    assert "(1a2b3c4d)" in used.inner_text()
    assert used.get_by_role("link", name="Remove").count() == 0
    assert "No schedule" in spare.inner_text()
    remove = spare.get_by_role("link", name="Remove Please respond")
    assert remove.get_attribute("href") == "/content-edit/spare#id_clear"
    # The confirmation email has no schedules: no Sent by column, no Remove.
    confirmation = page.get_by_role("table", name="Confirmation email")
    assert "Sent by" not in confirmation.inner_text()
    assert confirmation.get_by_role("link", name="Remove").count() == 0
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert axe_violations(page, axe_source) == []
