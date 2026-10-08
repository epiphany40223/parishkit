"""Admin controls act in place: no reload, no jump to the top (#519).

Each test sets a mark outside every region, which only a full page load
could lose, scrolls so the control sits well below the fold, activates it,
and checks the mark, the scroll position, the new content, focus, the live
region and the address. The Refresh links are the real Background work and
Families on the form now templates; the form tests use a small test-only page
(in_place_components.py) around one form[data-in-place].
"""

import pytest

from .in_place_components import BACKGROUND, FORM, PRESENCE
from .waits import eventually, has_text, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

VIEWPORT = {"width": 1280, "height": 500}
MARK = "document.querySelector('h1').dataset.mark = 'kept'"
MARKED = "document.querySelector('h1').dataset.mark"


def scroll_below(page, selector):
    """Scroll so ``selector`` sits mid-viewport; return the (non-zero) offset."""
    page.locator(selector).scroll_into_view_if_needed()
    page.evaluate(
        "selector => window.scrollTo(0, document.querySelector(selector)"
        ".getBoundingClientRect().top + window.scrollY - 200)",
        selector,
    )
    offset = page.evaluate("window.scrollY")
    assert offset > 0, "the control must sit below the fold for this test"
    return offset


def count_requests(page, method, part):
    """Collect every ``method`` request whose URL contains ``part``."""
    seen = []
    page.on(
        "request",
        lambda request: (
            seen.append(request.url)
            if request.method == method and part in request.url
            else None
        ),
    )
    return seen


def test_background_refresh_rereads_the_view_in_place(page, component_origin):
    """Refresh current work fetches the same page of the same view once and
    swaps the table and the work counts; the reader keeps their place and
    focus, and the live region says the list was refreshed."""
    page.set_viewport_size(VIEWPORT)
    page.goto(component_origin + BACKGROUND)
    page.evaluate(MARK)
    refresh = page.get_by_role("link", name="Refresh current work")
    href = refresh.get_attribute("href")
    assert "page=1" in href and href.endswith("#table")
    offset = scroll_below(page, "#background-refresh")
    gets = count_requests(page, "GET", "/admin/system/background/?")
    refresh.click()
    has_text(page.locator("#background-counts"), _counts(2))
    assert page.evaluate(MARKED) == "kept"
    assert abs(page.evaluate("window.scrollY") - offset) < 40
    assert page.evaluate("document.activeElement.id") == "background-refresh"
    has_text(
        page.get_by_role("status").filter(has_text="List refreshed."),
        "List refreshed. Showing 1–1 of 51 · Page 1 of 2",
    )
    assert page.url == component_origin + href
    assert len(gets) == 1


def _counts(active):
    """The background page's work-count line with ``active`` tasks running."""
    return (
        f"Running: {active} · Waiting to start: 0 · Waiting to try again: 0 · "
        "Stopped unexpectedly: 0"
    )


def test_background_refresh_follows_the_tables_page(page, component_origin):
    """After Next, the Refresh link (a sync node) carries page 2, so a refresh
    re-reads the page the reader is on rather than the first."""
    page.set_viewport_size(VIEWPORT)
    page.goto(component_origin + BACKGROUND)
    page.locator("#table .table-nav").first.get_by_role("link", name="Next").click()
    has_text(page.locator("#table tbody th[scope=row] a"), "ParishSoft data refresh 2")
    refresh = page.get_by_role("link", name="Refresh current work")
    assert "page=2" in refresh.get_attribute("href")
    page.evaluate(MARK)
    with page.expect_request(lambda request: "page=2" in request.url) as fetched:
        refresh.click()
    assert fetched.value.headers.get("x-requested-with") == "fetch"
    visible(page.get_by_role("status").filter(has_text="List refreshed."))
    assert page.evaluate(MARKED) == "kept"


def test_presence_refresh_updates_the_list_and_count_in_place(page, component_origin):
    """Refresh list swaps the rows, the count and the time outside the table."""
    page.set_viewport_size({"width": 1280, "height": 400})
    page.goto(component_origin + PRESENCE)
    page.evaluate(MARK)
    assert page.locator("#table tbody tr").count() == 1
    offset = scroll_below(page, "#table-refresh")
    gets = count_requests(page, "GET", "/admin/mail/presence/?size")
    page.get_by_role("link", name="Refresh list").click()
    has_text(
        page.locator("#presence-count"),
        "Families with the Family form open in their browser within the last "
        "90 seconds: 2",
    )
    assert page.locator("#table tbody tr").count() == 2
    assert page.evaluate(MARKED) == "kept"
    # The new row pushed the link down; the page never jumps back up, and
    # scrolls (only) enough to keep the focused link in view.
    assert page.evaluate("window.scrollY") >= offset
    assert page.evaluate(
        "(() => { const box = document.getElementById('table-refresh')"
        ".getBoundingClientRect(); return box.top >= 0"
        " && box.bottom <= window.innerHeight; })()"
    )
    assert page.evaluate("document.activeElement.id") == "table-refresh"
    assert page.url.endswith("sort=-heartbeat#table")
    has_text(
        page.get_by_role("status").filter(has_text="List refreshed."),
        "List refreshed. Showing 1–2 of 2 · Page 1 of 1",
    )
    assert len(gets) == 1


def test_post_form_saves_in_place_with_one_request(page, component_origin):
    """A data-in-place POST follows the server's redirect back to this page
    and swaps the region: one POST per click, even for a double click, the
    reader's place kept, focus back on the fresh button, the save announced,
    and the address set to the redirect's GET so a reload never re-posts."""
    page.set_viewport_size(VIEWPORT)
    page.goto(component_origin + FORM)
    page.evaluate(MARK)
    offset = scroll_below(page, "#saved")
    posts = count_requests(page, "POST", "/in-place-form/")
    page.locator("#note").fill("hello")
    page.locator("#save").dblclick()
    has_text(page.locator("#saved-count"), "Saved 1 times")
    assert page.evaluate(MARKED) == "kept"
    assert abs(page.evaluate("window.scrollY") - offset) < 40
    assert page.evaluate("document.activeElement.id") == "save"
    has_text(page.get_by_role("status").filter(has_text="Saved."), "Saved.")
    assert page.url == component_origin + FORM + "?saved=1#saved"
    assert posts == [component_origin + FORM + "/save"]


def test_post_form_sends_the_body_a_native_submission_would(page, component_origin):
    """The fields and the clicked button travel urlencoded, with the fetch
    marker the server can see."""
    page.goto(component_origin + FORM)
    page.locator("#note").fill("a&b")
    with page.expect_request(lambda request: request.method == "POST") as sent:
        page.locator("#save").click()
    request = sent.value
    assert request.headers.get("x-requested-with") == "fetch"
    assert request.post_data == "csrfmiddlewaretoken=" + "a" * 64 + (
        "&note=a%26b&action=save"
    )
    has_text(page.locator("#saved-count"), "Saved 1 times")


@pytest.mark.parametrize("button", ["refuse", "invalid"])
def test_refused_save_is_swapped_into_its_region(page, component_origin, button):
    """A refused save answered with this page again (a 400 with the summary
    in the region, or a 200 with it where base.html draws it, outside every
    region) swaps only the regions (#562): the reader keeps their place,
    the summary sits at the top of the region and takes focus, the value
    the server re-rendered stays, the error messages are announced (focus on
    the summary already reads its heading), the address is unchanged, and the
    POST is sent once."""
    page.set_viewport_size(VIEWPORT)
    page.goto(component_origin + FORM)
    page.evaluate(MARK)
    offset = scroll_below(page, "#saved")
    posts = count_requests(page, "POST", "/in-place-form/")
    page.locator("#note").fill("hello")
    page.locator(f"#{button}").click()
    summary = page.locator("#saved > [data-error-summary]:first-child")
    has_text(summary.locator("li"), "Enter a shorter note.")
    # Only the regions changed: this page's own heading and mark remain.
    assert page.locator("h1").inner_text() == "In-place form"
    assert page.evaluate(MARKED) == "kept"
    assert abs(page.evaluate("window.scrollY") - offset) < 40
    assert page.evaluate("document.activeElement.matches('[data-error-summary]')")
    assert page.locator("[data-error-summary]").count() == 1
    assert page.locator("#note").input_value() == "hello"
    has_text(
        page.get_by_role("status").filter(has_text="shorter"), "Enter a shorter note."
    )
    assert page.url == component_origin + FORM
    assert posts == [component_origin + FORM + f"/{button}"]
    # The swapped summary's link moves focus to its field.
    summary.get_by_role("link", name="Enter a shorter note.").click()
    assert page.evaluate("document.activeElement.id") == "note"
    # The next save is in place as before, and the summary goes.
    page.locator("#save").click()
    has_text(page.locator("#saved-count"), "Saved 1 times")
    assert page.locator("[data-error-summary]").count() == 0


def test_successful_swap_keeps_a_summary_drawn_outside_the_regions(
    page, component_origin
):
    """A summary an ordinary form's refusal drew at the top of the page
    (outside every region) stays through a successful in-place save: only a
    refusal replaces it."""
    page.goto(component_origin + FORM + "?error=1")
    outside = page.locator("main > [data-error-summary]")
    visible(outside)
    page.locator("#save").click()
    has_text(page.locator("#saved-count"), "Saved 1 times")
    visible(outside)
    assert page.locator("[data-error-summary]").count() == 1


# Counts each interval's firings, across the whole-page write: document.open
# keeps the window, so this wrapper (and the old page's timers) survive it.
COUNT_INTERVALS = """
if (!window.__intervals) {
  const original = window.setInterval.bind(window);
  window.__intervals = [];
  window.setInterval = (callback, delay, ...rest) => {
    const entry = {delay, fired: 0};
    window.__intervals.push(entry);
    return original(() => { entry.fired += 1; callback(...rest); }, delay);
  };
}
"""
FIRED = "window.__intervals.map((entry) => entry.fired)"
TICKED = "window.__intervals.some((entry) => entry.delay === 1000 && entry.fired > 0)"


def test_refusal_without_the_region_is_shown_whole_and_stops_old_timers(
    page, component_origin
):
    """A refused save answered with another page (a denial) is shown as the
    whole page, sent once and never re-fetched; none of the old page's timers
    run afterwards, so the new page's session tick is the only one."""
    page.add_init_script(COUNT_INTERVALS)
    page.goto(component_origin + FORM)
    # The old page's own one-second session tick (session-v1.js) is running.
    # (Polled from here: the strict policy refuses wait_for_function's eval.)
    for _ in range(50):
        if page.evaluate(TICKED):
            break
        page.wait_for_timeout(100)
    assert page.evaluate(TICKED)
    old = page.evaluate("window.__intervals.length")
    requests = []
    page.on(
        "request",
        lambda request: (
            requests.append(request.method) if "/denied" in request.url else None
        ),
    )
    page.locator("#denied").click()
    has_text(page.locator("h1"), "Denied page")
    before = page.evaluate(FIRED)[:old]
    page.wait_for_timeout(2500)
    entries = page.evaluate("window.__intervals")
    assert [entry["fired"] for entry in entries[:old]] == before, (
        "an old page's timer still runs"
    )
    ticks = [entry["fired"] for entry in entries[old:] if entry["delay"] == 1000]
    assert len(ticks) == 1 and ticks[0] >= 1, "the new page runs one session tick"
    assert requests == ["POST"]


def test_post_form_network_failure_says_so_without_resending(page, component_origin):
    """A POST that got no answer may have been saved, so it is not replayed:
    the form says the server could not be reached, the page stays, and the
    next submission clears the note."""
    page.set_viewport_size(VIEWPORT)
    page.goto(component_origin + FORM)
    page.evaluate(MARK)
    attempts = []

    def fail(route):
        attempts.append(route.request.method)
        route.abort()

    page.route("**/in-place-form/save", fail)
    page.locator("#save").click()
    alert = page.get_by_role("alert").filter(has_text="could not be reached")
    visible(alert)
    assert attempts == ["POST"]
    assert page.evaluate(MARKED) == "kept"
    assert page.locator("#saved-count").inner_text() == "Saved 0 times"
    page.unroute("**/in-place-form/save")
    page.locator("#save").click()
    has_text(page.locator("#saved-count"), "Saved 1 times")
    visible(page.locator("#saved"))
    assert page.locator("[data-in-place-error]").count() == 0


# Tells the page the reader is leaving it, as a navigation or a download
# link does before the browser cuts its requests off.
LEAVING = "window.dispatchEvent(new Event('beforeunload'))"


def test_leaving_the_page_keeps_a_save_note_but_skips_a_read_fallback(
    page, component_origin
):
    """While the page is being left, a read with no answer does not fall back
    to its own load (that would hijack the reader's navigation), but a save
    with no answer still says the server could not be reached: a download
    link also fires beforeunload, and its page stays."""
    page.set_viewport_size(VIEWPORT)
    page.goto(component_origin + BACKGROUND)
    page.evaluate(MARK)
    page.route("**/admin/background?*", lambda route: route.abort())
    page.evaluate(LEAVING)
    page.get_by_role("link", name="Refresh current work").click()
    eventually(page, "() => !document.querySelector('[aria-busy=true]')")
    assert page.evaluate(MARKED) == "kept"
    page.goto(component_origin + FORM)
    page.evaluate(MARK)
    page.route("**/in-place-form/save", lambda route: route.abort())
    page.evaluate(LEAVING)
    page.locator("#save").click()
    visible(page.get_by_role("alert").filter(has_text="could not be reached"))
    assert page.evaluate(MARKED) == "kept"


def test_post_form_redirect_to_another_page_navigates(page, component_origin):
    """A POST whose redirect lands on another page shows that page (its GET,
    safe to repeat), even though it carries a region with the same id."""
    page.goto(component_origin + FORM)
    posts = count_requests(page, "POST", "/in-place-form/")
    page.evaluate(MARK)
    page.locator("#elsewhere").click()
    page.wait_for_url(component_origin + "/in-place-other#saved")
    has_text(page.locator("h1"), "Another page")
    assert page.evaluate(MARKED) is None
    assert posts == [component_origin + FORM + "/elsewhere"]


def test_external_submitter_save_is_never_resent(page, component_origin):
    """A save whose button sits outside its form (form="…") is handled as a
    save too: with no answer it shows the note and is not re-sent natively."""
    page.goto(component_origin + FORM)
    page.evaluate(MARK)
    attempts = []

    def fail(route):
        attempts.append(route.request.method)
        route.abort()

    page.route("**/in-place-form/save", fail)
    page.locator("#outside-save").click()
    visible(page.locator("#outside-form [data-in-place-error][role=alert]"))
    assert attempts == ["POST"]
    assert page.evaluate(MARKED) == "kept"
    assert page.url == component_origin + FORM


def test_external_submitter_saves_in_place(page, component_origin):
    """A save whose button sits outside its form (form="…") belongs to that
    form: it saves in place, focus returns to the fresh button, and the
    form's own data-in-place-message is announced (not a table count)."""
    page.set_viewport_size(VIEWPORT)
    page.goto(component_origin + FORM)
    page.evaluate(MARK)
    posts = count_requests(page, "POST", "/in-place-form/")
    page.locator("#outside-save").click()
    has_text(page.locator("#saved-count"), "Saved 1 times")
    assert page.evaluate(MARKED) == "kept"
    assert page.evaluate("document.activeElement.id") == "outside-save"
    has_text(
        page.get_by_role("status").filter(has_text="Saved from outside."),
        "Saved from outside.",
    )
    assert page.url == component_origin + FORM + "?saved=1#saved"
    assert posts == [component_origin + FORM + "/save"]


def test_sort_heading_inside_an_in_place_form_stays_a_table_control(
    page, component_origin
):
    """A table's sort heading inside a form[data-in-place] (a selection form
    around its table) re-sorts the table in place: focus returns to the
    fresh heading and the sort is announced, never the form's message."""
    page.set_viewport_size(VIEWPORT)
    page.goto(component_origin + FORM)
    page.evaluate(MARK)
    with page.expect_request(lambda request: "sort=-name" in request.url) as fetched:
        page.locator("#picks .sort-link").click()
    assert fetched.value.headers.get("x-requested-with") == "fetch"
    has_text(
        page.get_by_role("status").filter(has_text="Sorted by"),
        "Sorted by Name, descending",
    )
    assert page.evaluate(MARKED) == "kept"
    assert page.evaluate(
        "document.activeElement.matches('#picks th[data-sort-column=name] .sort-link')"
    )
    assert page.url == component_origin + FORM + "?sort=-name#picks"


def test_post_answer_without_the_region_is_shown_not_refetched(page, component_origin):
    """A POST answered directly (no redirect) by a page without the region is
    shown as returned; its address is never loaded again as a GET."""
    page.goto(component_origin + FORM)
    requests = []
    page.on(
        "request",
        lambda request: (
            requests.append(request.method) if "/plain" in request.url else None
        ),
    )
    page.locator("#plain").click()
    has_text(page.locator("h1"), "Administration sign-in")
    assert requests == ["POST"]


def test_get_form_applies_in_place_and_falls_back_to_its_url(page, component_origin):
    """A data-in-place GET form is fetched and swapped in like a link; when
    the fetch fails it loads the very URL instead."""
    page.set_viewport_size(VIEWPORT)
    page.goto(component_origin + FORM)
    page.evaluate(MARK)
    with page.expect_request(lambda request: "saved=1" in request.url) as fetched:
        page.locator("#show").click()
    assert fetched.value.headers.get("x-requested-with") == "fetch"
    has_text(page.locator("#saved-count"), "Saved 1 times")
    assert page.evaluate(MARKED) == "kept"
    assert page.url == component_origin + FORM + "?saved=1#saved"
    assert page.evaluate("document.activeElement.id") == "show"
    page.goto(component_origin + FORM)
    page.evaluate(MARK)
    page.route(
        "**/in-place-form?saved=1",
        lambda route: (
            route.abort()
            if route.request.headers.get("x-requested-with") == "fetch"
            else route.continue_()
        ),
    )
    page.locator("#show").click()
    page.wait_for_url(component_origin + FORM + "?saved=1#saved")
    has_text(page.locator("#saved-count"), "Saved 1 times")
    assert page.evaluate(MARKED) is None


def test_submitter_formenctype_and_formmethod_are_honoured(page, component_origin):
    """A button's formenctype sends multipart; a button's formmethod turns a
    GET form's submission into the POST a native submission would send."""
    page.goto(component_origin + FORM)
    with page.expect_request(lambda request: request.method == "POST") as sent:
        page.locator("#multipart").click()
    assert sent.value.headers["content-type"].startswith("multipart/form-data")
    has_text(page.locator("#saved-count"), "Saved 1 times")
    page.goto(component_origin + FORM)
    with page.expect_request(lambda request: request.method == "POST") as sent:
        page.locator("#show-post").click()
    assert sent.value.url == component_origin + FORM + "/save"
    has_text(page.locator("#saved-count"), "Saved 1 times")


def test_controls_are_ignored_while_a_save_is_in_flight(page, component_origin):
    """While a save runs its button looks busy, another in-place control does
    nothing but say so, and the save then completes and is reported."""
    from playwright.sync_api import expect

    page.goto(component_origin + FORM)
    gets = count_requests(page, "GET", "/in-place-form?saved=1")
    page.locator("#slow").click()
    expect(page.locator("#slow")).to_have_class("is-busy")
    page.locator("#show").click()
    has_text(
        page.get_by_role("status").filter(has_text="Still saving"), "Still saving…"
    )
    has_text(page.locator("#saved-count"), "Saved 1 times")
    has_text(page.get_by_role("status").filter(has_text="Saved."), "Saved.")
    # Only the save's own redirect fetched the page; the ignored click did not.
    assert len(gets) == 1


def wait_for_routes(page, held, count):
    """Let the page run until ``count`` held requests have reached the route."""
    for _ in range(50):
        if len(held) >= count:
            return
        page.wait_for_timeout(100)
    raise AssertionError(f"only {len(held)} of {count} requests arrived")


def test_an_aborted_request_leaves_the_newer_ones_busy_mark(page, component_origin):
    """A second choice aborts the first; the aborted request must not clear
    the busy mark the newer request set on the same region, and the mark
    goes once the newer request completes."""
    page.goto(component_origin + FORM)
    held = []
    page.route("**/in-place-form?sort=-name", lambda route: held.append(route))
    heading = page.locator("#picks .sort-link")
    heading.click()
    wait_for_routes(page, held, 1)
    heading.click()
    wait_for_routes(page, held, 2)
    assert page.locator("#picks").get_attribute("aria-busy") == "true"
    held[1].continue_()
    has_text(
        page.get_by_role("status").filter(has_text="Sorted by"),
        "Sorted by Name, descending",
    )
    assert page.locator("#picks").get_attribute("aria-busy") is None


def test_an_aborted_request_clears_its_own_region_busy_mark(page, component_origin):
    """When the newer request changes a different region, the aborted one
    clears its own region's busy mark at once, rather than leaving it to a
    swap that might never include that region."""
    page.goto(component_origin + FORM)
    held = []
    page.route("**/in-place-form?sort=-name", lambda route: held.append(route))
    page.route("**/in-place-form?saved=1", lambda route: held.append(route))
    page.locator("#picks .sort-link").click()
    wait_for_routes(page, held, 1)
    assert page.locator("#picks").get_attribute("aria-busy") == "true"
    page.locator("#show").click()
    wait_for_routes(page, held, 2)
    assert page.locator("#saved").get_attribute("aria-busy") == "true"
    assert page.locator("#picks").get_attribute("aria-busy") is None
    held[1].continue_()
    has_text(page.locator("#saved-count"), "Saved 1 times")
    assert page.locator("#saved").get_attribute("aria-busy") is None


def test_a_submission_without_submitter_focuses_an_outside_button(
    page, component_origin
):
    """A form submitted from script (no submitter) stands for its first
    submit button, even one outside it (form="…"), so focus returns there."""
    page.goto(component_origin + FORM)
    page.evaluate("document.getElementById('outside-form').requestSubmit()")
    has_text(page.locator("#saved-count"), "Saved 1 times")
    assert page.evaluate("document.activeElement.id") == "outside-save"
