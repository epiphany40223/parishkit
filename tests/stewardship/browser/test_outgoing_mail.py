"""Outgoing mail's sending limit note and bulk actions in the browser (#382)."""

import pytest

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)


def test_the_sending_limit_note_names_each_hold_in_local_time(page, component_origin):
    """Above the list, outside its in-place region, linking System health."""
    page.goto(component_origin + "/deliveries-holds")
    note = page.locator("#delivery-holds")
    assert note.is_visible()
    text = note.inner_text()
    assert "daily limit for Family email" in text
    assert "Gmail's own sending limit" in text
    assert "2 Family emails are waiting longer than usual to retry" in text
    # Browser-local times (the fixture browser is in Los Angeles).
    times = note.locator("time[data-local-instant]").all_inner_texts()
    assert len(times) == 2
    assert "5:40" in times[0] and "PDT" in times[0]
    assert "5:15" in times[1] and "PDT" in times[1]
    assert (
        note.get_by_role("link", name="See System health for details").get_attribute(
            "href"
        )
        == "/admin/system/health/"
    )
    # Drawn once per page load: never swapped in or out by an in-place control.
    assert note.evaluate(
        "node => !node.closest('[data-table-region], [data-in-place-region]')"
    )
    # The note precedes the list.
    assert note.evaluate(
        "node => Boolean(node.compareDocumentPosition("
        "document.querySelector('[data-table-region]'))"
        " & Node.DOCUMENT_POSITION_FOLLOWING)"
    )


def test_no_note_while_nothing_is_held(page, component_origin):
    """The ordinary page is unchanged."""
    page.goto(component_origin + "/deliveries")
    assert page.locator("#delivery-holds").count() == 0
