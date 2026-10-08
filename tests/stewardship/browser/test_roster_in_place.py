"""Roster changes to enter ticks and pages in place (#528, step 4).

The real list is served by ``roster_components`` and the request page by
``followup_components``. A mark outside every region shows the page never
reloaded.
"""

import pytest

from .followup_components import TICK_ITEM
from .roster_components import PATH
from .test_table_sorting import MARK, MARKED
from .waits import has_text, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

NAMES = "#table tbody th[scope=row] a"


def test_a_tick_and_the_pager_act_in_place(page, component_origin):
    """Mark entered in ParishSoft redirects back to the list, which no longer
    shows the row; Next pages the list; neither reloads the page."""
    page.goto(component_origin + PATH)
    page.evaluate(MARK)
    assert page.locator(NAMES).first.inner_text() == "Member 29"
    with page.expect_request(lambda request: request.method == "POST") as sent:
        page.locator("#table").get_by_role(
            "button", name="Mark entered in ParishSoft"
        ).first.click()
    body = sent.value.post_data
    assert "entered=yes" in body and "sequence=1" in body and "return_to=roster" in body
    # The tick carries the list's own view, so its answer keeps it.
    assert "show=todo" in body and "size=25" in body and "page=1" in body
    has_text(page.locator(NAMES).first, "Member 28")
    assert page.evaluate(MARKED) == "kept"
    page.goto(component_origin + PATH)
    page.evaluate(MARK)
    page.locator("#table").get_by_role("link", name="Next").first.click()
    has_text(page.locator(NAMES).first, "Member 04")
    assert page.evaluate(MARKED) == "kept"


def test_show_all_and_a_tick_there_stay_on_all(page, component_origin):
    """The Show filter refreshes in place, and a tick on All comes back to All
    with the row now marked, never to the default view."""
    page.goto(component_origin + PATH)
    page.evaluate(MARK)
    page.get_by_label("Show").select_option("all")
    page.get_by_role("button", name="Apply").click()
    has_text(page.locator(NAMES).nth(1), "Member 28")
    with page.expect_request(lambda request: request.method == "POST") as sent:
        page.locator("#table tbody tr").nth(1).get_by_role(
            "button", name="Mark entered in ParishSoft"
        ).click()
    assert "show=all" in sent.value.post_data
    visible(
        page.locator("#table tbody tr")
        .nth(1)
        .get_by_role("button", name="Clear Entered in ParishSoft")
    )
    assert page.get_by_label("Show").input_value() == "all"
    assert page.evaluate(MARKED) == "kept"


def test_the_request_page_ticks_in_place(page, component_origin):
    """The tick on a resolved request's own page answers in place."""
    page.goto(component_origin + TICK_ITEM)
    page.evaluate(MARK)
    visible(page.get_by_text("Not yet entered in ParishSoft"))
    page.get_by_role("button", name="Mark entered in ParishSoft").click()
    visible(page.get_by_text("Entered in ParishSoft by staff@example.org"))
    visible(page.get_by_role("button", name="Clear Entered in ParishSoft"))
    assert page.evaluate(MARKED) == "kept"


def test_a_stale_tick_on_the_list_announces_its_notice(page, component_origin):
    """A tick someone else changed first answers with the list and its notice,
    which is what is announced (never the success message), and the one-time
    changed=1 leaves the address."""
    page.goto(component_origin + PATH)
    page.evaluate(MARK)
    page.locator("#table tbody tr").nth(2).get_by_role(
        "button", name="Mark entered in ParishSoft"
    ).click()
    notice = "That mark was changed by someone else first, so nothing was recorded."
    has_text(
        page.locator("#roster-notice"),
        notice + " The list shows its current state.",
    )
    visible(page.get_by_role("status").filter(has_text=notice))
    assert page.get_by_role("status").filter(has_text="Marked entered").count() == 0
    assert "changed=1" not in page.url
    assert page.evaluate(MARKED) == "kept"
