"""Ministry requests' Stage History (#2180), as the other review tables have.

History lists the superseded and cancelled requests, read-only (no row
icons, no selection), each saying what ended it; Every stage leaves them
out. A Ministry leader gets the choice too (their scope is the selection's,
tested in the database). Served by ``followup_components``' ``HISTORY`` and
``HISTORY_LEADER``.
"""

import pytest

from .followup_components import (
    HISTORY,
    HISTORY_LEADER,
    PARISH_WITHDRAWN,
    REPLACED_REQUEST,
    REQUEST,
    WITHDRAWN_REQUEST,
)
from .test_ministry_followup import worklist_row

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)


@pytest.mark.parametrize("width", [320, 1280])
def test_history_lists_past_requests_read_only(page, component_origin, width):
    """History holds the three past requests with their reason, and no row
    offers an action; its count sits before Every stage, which counts only
    the current request."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + HISTORY)
    options = page.get_by_label("Stage").locator("option").all_inner_texts()
    assert options[-2:] == ["History (3)", "Every stage (1)"]
    assert page.get_by_label("Stage").input_value() == "history"
    assert worklist_row(page, REQUEST).count() == 0
    for request, reason in (
        (REPLACED_REQUEST, "Replaced by Family"),
        (WITHDRAWN_REQUEST, "Withdrawn by Family"),
        (PARISH_WITHDRAWN, "Withdrawn by the parish"),
    ):
        row = worklist_row(page, request)
        assert "History" in row.locator(".stage-chip").inner_text()
        assert reason in row.inner_text()
        assert row.locator(".table-actions button").count() == 0
        assert row.locator("input[type=checkbox]").count() == 0
    # No horizontal page scroll at either width.
    assert page.evaluate(
        "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
    )


def test_a_leader_has_history_too(page, component_origin):
    """A Ministry leader's Stage filter offers History after Confirmed."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + HISTORY_LEADER)
    options = page.get_by_label("Stage").locator("option").all_inner_texts()
    assert options[-3:] == ["Confirmed (0)", "History (2)", "Every stage (1)"]
    assert worklist_row(page, REPLACED_REQUEST).count() == 1
