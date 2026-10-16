"""Roster changes to enter ticks and pages in place (#528, step 4).

The real list is served by ``roster_components`` and the request page by
``followup_components``. A mark outside every region shows the page never
reloaded.
"""

import json
from uuid import UUID

import pytest
from django.http import QueryDict

from parishkit.stewardship.reports.ministry_roster_views import bulk_values

from .followup_components import TICK_ITEM
from .roster_components import BULK_KEY, BULK_POST, PATH, TWICE
from .test_components import axe_violations
from .test_table_sorting import MARK, MARKED
from .waits import available, has_text, hidden, same_place, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

NAMES = "#table tbody th[scope=row] a"
# Every accessible name and tooltip in a table, to find a stray UTC instant.
NAME_TEXT = (
    "node => [...node.querySelectorAll('[aria-label], [title]')]"
    ".map((el) => el.getAttribute('aria-label') + el.title).join()"
)


def test_a_tick_and_the_pager_act_in_place(page, component_origin):
    """Mark entered in ParishSoft redirects back to the list, which no longer
    shows the row; Next pages the list; neither reloads the page."""
    page.goto(component_origin + PATH)
    page.evaluate(MARK)
    assert page.locator(NAMES).first.inner_text() == "Member 29"
    with page.expect_request(lambda request: request.method == "POST") as sent:
        page.locator("#table").get_by_role(
            "button", name="Mark Member 29 — Food pantry entered in ParishSoft"
        ).click()
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
            "button", name="Mark Member 28 — Food pantry entered in ParishSoft"
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
        "button", name="Mark Member 27 — Food pantry entered in ParishSoft"
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


def newest(index):
    """Row ``index``'s request id in the fixture list (29 is the newest)."""
    return UUID(int=10_000 + index)


def bulk_start(page, component_origin):
    """Open the list, tick the two newest changes and open the bulk dialog.

    Returns the dialog and the script errors seen, which must stay empty.
    """
    page.goto(component_origin + PATH)
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.evaluate(MARK)
    bulk = page.locator(".bulk-bar").get_by_role(
        "button", name="Mark entered in ParishSoft"
    )
    # Nothing ticked: unavailable, and the reserved line says why.
    assert bulk.is_disabled()
    has_text(
        page.locator("[data-selection-hint]"),
        "Tick changes not yet entered on this page to mark them entered in ParishSoft.",
    )
    for index in (29, 28):
        page.get_by_label(f"Select Member {index} — Food pantry").check()
    available(bulk)
    bulk.click()
    dialog = page.locator("dialog#roster-bulk-dialog")
    visible(dialog)
    return dialog, failures


def test_the_row_tick_is_a_square_icon_button_named_for_its_row(page, component_origin):
    """Each row's tick is a same-size icon button in the Actions column,
    named and titled for its row (#879)."""
    page.goto(component_origin + PATH)
    button = page.get_by_role(
        "button", name="Mark Member 29 — Food pantry entered in ParishSoft"
    )
    assert button.get_attribute("title") == (
        "Mark Member 29 — Food pantry entered in ParishSoft"
    )
    assert button.locator("svg.action-icon").count() == 1
    box = button.bounding_box()
    assert abs(box["width"] - box["height"]) < 1
    assert button.evaluate("node => node.closest('td').className") == "table-actions"


def test_bulk_marks_the_ticked_changes_in_place(page, component_origin, axe_source):
    """Mark entered in ParishSoft asks first, naming the count, with Cancel
    focused; confirming posts each ticked change with its next sequence and
    the page's key, then redraws the list in place without a reload and
    announces what was marked."""
    dialog, failures = bulk_start(page, component_origin)
    assert dialog.locator("h2").inner_text() == (
        "Mark 2 changes entered in ParishSoft?"
    )
    assert page.evaluate("document.activeElement.textContent.trim()") == "Cancel"
    assert axe_violations(page, axe_source) == []
    posted = []

    def answer(route):
        """Record the dialog's POST and answer as the bulk view does."""
        posted.append(route.request.post_data or "")
        route.fulfill(status=204, body="")

    page.route(component_origin + BULK_POST, answer)
    dialog.get_by_role("button", name="Mark entered").click()
    hidden(dialog)
    visible(
        page.locator(
            "main > [role=status]",
            has_text="Marked 2 changes entered in ParishSoft.",
        )
    )
    has_text(page.locator(NAMES).first, "Member 27")
    assert page.evaluate(MARKED) == "kept"
    sequences, key = bulk_values(QueryDict(posted[0]))
    assert sequences == {newest(29): 1, newest(28): 1} and key == BULK_KEY
    # The redrawn list carries a fresh key, and its selection is wired again.
    assert page.locator("input[name=bulk_key]").input_value() != str(BULK_KEY)
    page.get_by_label("Select Member 27 — Food pantry").check()
    available(page.locator(".bulk-bar").get_by_role("button", name="Mark entered"))
    assert not failures


def test_a_refused_bulk_mark_names_the_row_inside_the_dialog(page, component_origin):
    """A change someone else marked first refuses the whole post: the
    server's words show below the buttons, which do not move, and nothing is
    redrawn; Cancel closes the dialog and returns focus to the bulk button."""
    dialog, failures = bulk_start(page, component_origin)
    message = (
        "Member 28 — Food pantry changed after this list was shown (someone "
        "else may have marked it), so nothing was marked."
    )

    def refuse(route):
        """Answer as the bulk view does for a change that moved on."""
        route.fulfill(
            status=409,
            content_type="application/json",
            body=json.dumps({"refusal": {"message": message, "fix": "Reload."}}),
        )

    page.route(component_origin + BULK_POST, refuse)
    accept = dialog.get_by_role("button", name="Mark entered")
    accept.scroll_into_view_if_needed()
    box = accept.bounding_box()
    accept.click()
    error = dialog.locator("[data-confirm-error]")
    visible(error)
    assert message in error.inner_text()
    assert same_place(accept.bounding_box(), box) and accept.is_disabled()
    assert page.locator("input[data-select-row]:checked").count() == 2
    dialog.get_by_role("button", name="Cancel").click()
    hidden(dialog)
    assert (
        page.evaluate("document.activeElement.textContent.trim()")
        == "Mark entered in ParishSoft"
    )
    assert page.locator(NAMES).first.inner_text() == "Member 29"
    assert page.evaluate(MARKED) == "kept"
    assert not failures


@pytest.mark.parametrize(
    ("zone", "days"),
    [
        ("America/New_York", ("Oct 4, 2054", "Oct 1, 2054")),
        ("Asia/Tokyo", ("Oct 5, 2054", "Oct 2, 2054")),
    ],
)
def test_a_change_listed_twice_names_its_browser_local_day(
    browser_engine, component_origin, zone, days
):
    """A change listed twice names each row by the day it was resolved in
    this browser's time zone (#558): its box, its tick's name and tooltip and
    the bulk dialog's title, for resolutions at 03:30 UTC, the evening before
    in New York. The bulk dialog posts the browser's zone, in which the
    server names a stale row's day."""
    context = browser_engine.new_context(timezone_id=zone)
    try:
        page = context.new_page()
        failures = []
        page.on("pageerror", lambda error: failures.append(str(error)))
        page.goto(component_origin + TWICE)
        rows = page.locator("#table tbody tr")
        for index, day in enumerate(days):
            name = f"Member 00 — Food pantry, resolved {day}"
            row = rows.nth(index)
            box = row.get_by_label(f"Select {name}", exact=True)
            assert box.get_attribute("data-confirm-name") == name
            tick = row.get_by_role(
                "button", name=f"Mark {name} entered in ParishSoft", exact=True
            )
            assert tick.get_attribute("title") == f"Mark {name} entered in ParishSoft"
        # No UTC instant is left in any name.
        assert "+00:00" not in page.locator("#table").evaluate(NAME_TEXT)
        # A tick refreshes the table in place: the row left in the swapped-in
        # table is named by the browser's day too, not the server's instant.
        page.evaluate(MARK)
        rows.nth(0).get_by_role(
            "button",
            name=f"Mark Member 00 — Food pantry, resolved {days[0]} "
            "entered in ParishSoft",
        ).click()
        has_text(page.locator(NAMES).first, "Member 00")
        assert rows.count() == 1
        assert page.evaluate(MARKED) == "kept"
        name = f"Member 00 — Food pantry, resolved {days[1]}"
        assert rows.first.get_by_label(f"Select {name}", exact=True).count() == 1
        assert (
            rows.first.get_by_role(
                "button", name=f"Mark {name} entered in ParishSoft", exact=True
            ).get_attribute("title")
            == f"Mark {name} entered in ParishSoft"
        )
        assert "+00:00" not in page.locator("#table").evaluate(NAME_TEXT)
        rows.nth(0).get_by_role("checkbox").check()
        page.locator(".bulk-bar").get_by_role(
            "button", name="Mark entered in ParishSoft"
        ).click()
        dialog = page.locator("dialog#roster-bulk-dialog")
        visible(dialog)
        assert dialog.locator("h2").inner_text() == (
            f"Mark Member 00 — Food pantry, resolved {days[1]} entered in ParishSoft?"
        )
        posted = []

        def answer(route):
            """Record the dialog's POST and answer as the bulk view does."""
            posted.append(route.request.post_data or "")
            route.fulfill(status=204, body="")

        page.route(component_origin + BULK_POST, answer)
        dialog.get_by_role("button", name="Mark entered").click()
        hidden(dialog)
        form = QueryDict(posted[0])
        assert form.getlist("zone") == [zone]
        assert bulk_values(form)[1] == BULK_KEY
        assert not failures
    finally:
        context.close()
