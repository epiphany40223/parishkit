"""Refresh on the remaining live-status pages acts in place (#519 PR 7b).

Background task, Change status and both report export pages are served at
their real addresses (live_status_components), where their Refresh links
lead. Each test sets a mark outside every region, which only a full page
load could lose, scrolls the link below the fold with the mouse wheel (a
scripted scroll can be undone by Firefox, #1088), and checks the mark, the
scroll position, the fresh status, focus, the announcement and that exactly
one in-place read was sent.
"""

import pytest

from .live_status_components import CHANGE, EXACT, EXPORT, TASK_PAGE
from .test_in_place import MARK, MARKED
from .test_live_status_in_place import answer_reads
from .waits import has_attribute, has_text, hidden, painted, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

# Short enough that every page's Refresh link starts below the fold.
VIEWPORT = {"width": 1280, "height": 320}


def wheel_below(page, selector, *, attempts=5):
    """Wheel the page until ``selector`` sits 200 px below the viewport's top,
    or as far as the page scrolls, and holds there; return that scrollY.

    The wheel, not a script: Firefox can re-apply an earlier scroll into view
    at a later refresh and undo a scripted one (#1088). A wheel turn can stop
    short, so the page is wheeled until it holds across two settled checks.
    """
    where = (
        "Math.min(document.documentElement.scrollHeight"
        " - document.documentElement.clientHeight,"
        f" scrollY + document.querySelector({selector!r}).getBoundingClientRect().top"
        " - 200)"
    )
    there = f"Math.abs(({where}) - scrollY) <= 1"
    # Over the main content, so the wheel scrolls the page, not the menu.
    page.mouse.move(900, 200)
    for _ in range(attempts):
        page.mouse.wheel(0, page.evaluate(f"({where}) - scrollY"))
        painted(page)
        if not page.evaluate(there):
            continue
        painted(page)
        if page.evaluate(there):
            offset = page.evaluate("scrollY")
            assert offset > 0, "the link must start below the fold for this test"
            return offset
    raise AssertionError(f"the page did not stay below {selector}")


def in_place_reads(page):
    """Collect every read ui-v1.js sends in place (it marks them with
    X-Requested-With: fetch; live-status-v1.js's polls carry no such mark)."""
    seen = []
    page.on(
        "request",
        lambda request: (
            seen.append(request.url)
            if request.headers.get("x-requested-with") == "fetch"
            else None
        ),
    )
    return seen


def refresh(page, link, message):
    """Click the Refresh link named ``link`` and wait for ``message``; check
    that the page neither reloaded nor scrolled, and that focus stayed on
    the link."""
    selector = f"a[data-in-place='refresh']:text-is({link!r})"
    offset = wheel_below(page, "a[data-in-place='refresh']")
    reads = in_place_reads(page)
    page.locator(selector).click()
    has_text(page.get_by_role("status").filter(has_text=message), message)
    assert page.evaluate(MARKED) == "kept"
    # The page never jumps back up: it stays where the reader was (or as far
    # down as a shorter page still goes), and the link stays in view, which
    # may scroll the page down when the fresh content above it is taller.
    lowest = page.evaluate(
        "document.documentElement.scrollHeight - document.documentElement.clientHeight"
    )
    assert page.evaluate("scrollY") >= min(offset, lowest) - 40
    assert page.evaluate(
        "selector => { const box = document.querySelector(selector)"
        ".getBoundingClientRect(); return box.top >= 0"
        " && box.bottom <= document.documentElement.clientHeight; }",
        selector.replace(f":text-is({link!r})", ""),
    )
    assert page.evaluate("document.activeElement.textContent") == link
    return reads


def test_export_refresh_is_in_place(page, component_origin):
    """A failed export, held by other campaign work, is refreshed into a
    ready file in place: the panel and the campaign-work notice change
    together, and the ready file waits for Download (no automatic POST, as
    on an ordinary load)."""
    page.set_viewport_size(VIEWPORT)
    page.goto(component_origin + EXPORT)
    page.evaluate(MARK)
    visible(page.locator("#export-locked"))
    answer_reads(page, component_origin, EXPORT, "/live-export-ready")
    posts = []
    page.on(
        "request",
        lambda request: posts.append(request.url) if request.method == "POST" else None,
    )
    reads = refresh(page, "Refresh status", "Status refreshed.")
    visible(page.get_by_role("button", name="Download export"))
    hidden(page.locator("#export-locked"))
    assert len(reads) == 1
    assert page.url == component_origin + EXPORT + "#export-status"
    page.wait_for_timeout(1000)
    assert posts == []


def test_exact_export_refresh_is_in_place(page, component_origin):
    """A latest-data export's Refresh brings its ready figures in place."""
    page.set_viewport_size(VIEWPORT)
    page.goto(component_origin + EXACT)
    page.evaluate(MARK)
    visible(page.get_by_text("Calculating this export failed."))
    answer_reads(page, component_origin, EXACT, "/live-exact-ready")
    reads = refresh(page, "Refresh status", "Status refreshed.")
    visible(page.get_by_role("link", name="Open the file and download it"))
    assert len(reads) == 1


def test_change_status_refresh_is_in_place(page, component_origin):
    """Change status's Refresh brings the applied change in place, while the
    page is still watching it."""
    page.set_viewport_size(VIEWPORT)
    page.goto(component_origin + CHANGE)
    page.evaluate(MARK)
    visible(page.get_by_text("Applying your change…"))
    answer_reads(page, component_origin, CHANGE, "/live-change-applied")
    reads = refresh(page, "Refresh status", "Status refreshed.")
    visible(page.get_by_role("heading", name="Applied: your change is saved"))
    assert len(reads) == 1
    # One watcher, and so one status line, for the fresh region.
    assert page.locator(".live-status-problem").count() <= 1


def test_refresh_task_keeps_details_open_and_updates_them(page, component_origin):
    """Refresh task brings the finished run, its history and its technical
    details in place; the open Technical details stay open."""
    page.set_viewport_size(VIEWPORT)
    page.goto(component_origin + TASK_PAGE)
    page.evaluate(MARK)
    details = page.locator("details.technical-details")
    details.locator("summary").click()
    has_text(details.locator("#task-details code").first, "source_refresh")
    visible(details.get_by_text("running", exact=True))
    reads = refresh(page, "Refresh task", "Task refreshed.")
    visible(page.get_by_text("Finished successfully."))
    visible(details.get_by_text("succeeded", exact=True))
    assert details.evaluate("node => node.open")
    history = page.locator("[data-table-region]")
    visible(history.get_by_role("cell", name="Finished", exact=True))
    assert len(reads) == 1
    assert page.url.endswith("#task-status")


def test_refresh_task_keeps_the_history_sort(page, component_origin):
    """After the history is sorted by Action in place, Refresh task keeps
    that sort: the link followed the table's choice, so the fresh history
    is still sorted by Action."""
    page.set_viewport_size(VIEWPORT)
    page.goto(component_origin + TASK_PAGE)
    page.evaluate(MARK)
    heading = page.locator("th[data-sort-column='action']")
    heading.locator(".sort-link").click()
    has_attribute(heading, "aria-sort", "ascending")
    link = page.locator("#task-refresh")
    assert "sort=action" in link.get_attribute("href")
    reads = refresh(page, "Refresh task", "Task refreshed.")
    visible(page.get_by_text("Finished successfully."))
    has_attribute(heading, "aria-sort", "ascending")
    assert len(reads) == 1 and "sort=action" in reads[0]
