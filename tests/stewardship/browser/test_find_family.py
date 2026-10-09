"""The header's Find a Family box in real browsers (#561, NAV-19).

The page is ``find_family_components.py``; each test answers the box's
search route itself, so it can count the requests and read what they post.
Load (one request after a pause, none for one character), privacy (a POST
body, never a URL) and the keyboard path through the results are checked on
Chromium and WebKit.
"""

from contextlib import suppress
from urllib.parse import parse_qs

import pytest

from .find_family_components import ANSWERS, DIRECTORY, FIND, LONG, NONE, PATH
from .waits import has_text, hidden, recorded, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "webkit"], indirect=True
)


class _Seen(list):
    """The search requests a route saw, plus the ones it holds unanswered."""

    def __init__(self):
        super().__init__()
        self.held = []


def _answer(page, *, status=200, hold=(), statuses=None):
    """Answer the search route; return the list of requests it saw.

    Each entry is ``(method, url, fields)``. A search whose text is in
    ``hold`` is not answered: its route is kept in ``seen.held`` so the
    test can answer it late with ``_late``. ``statuses`` lists the status
    of each answer in turn (then 200); ``status`` answers every one.
    """
    seen = _Seen()
    queue = list(statuses or ())

    def handle(route):
        """Record the request and answer with the matching results."""
        request = route.request
        fields = parse_qs(request.post_data or "")
        seen.append((request.method, request.url, fields))
        text = fields.get("search", [""])[0]
        if text in hold:
            seen.held.append((route, text))
            return
        code = queue.pop(0) if queue else status
        route.fulfill(
            status=code,
            content_type="text/html; charset=utf-8",
            body=ANSWERS.get(text, NONE) if code == 200 else "",
        )

    page.route("**" + FIND + "*", handle)
    return seen


def _late(seen):
    """Answer every held search now, as a slow server would; an aborted
    request can no longer be answered, which is fine."""
    from playwright.sync_api import Error

    for route, text in seen.held:
        # The page aborted the request: there is nothing left to answer.
        with suppress(Error):
            route.fulfill(
                status=200,
                content_type="text/html; charset=utf-8",
                body=ANSWERS.get(text, NONE),
            )
    seen.held.clear()


def _box(page):
    """The search field, its results region and its live status."""
    field = page.get_by_role("searchbox", name="Find a Family")
    return (
        field,
        page.locator("#find-family-results"),
        page.locator("[data-find-family-status]"),
    )


def test_typing_searches_once_after_a_pause_by_post(page, component_origin):
    """One character sends nothing; fast typing sends one POST, not a URL."""
    seen = _answer(page)
    page.goto(component_origin + PATH)
    field, results, status = _box(page)
    field.press_sequentially("e")
    page.wait_for_timeout(700)
    assert seen == []
    field.press_sequentially("xamp", delay=40)
    recorded(page, seen, 1)
    method, url, fields = seen[0]
    assert method == "POST" and url == component_origin + FIND
    assert fields["search"] == ["examp"] and fields["csrfmiddlewaretoken"]
    visible(results)
    has_text(status, "12 Families found")
    assert results.get_by_role("link").count() == 8
    visible(
        results.get_by_role(
            "button", name="See all 12 in the active parishioner family directory"
        )
    )
    # The text never reached the address bar.
    assert page.url == component_origin + PATH
    page.wait_for_timeout(500)
    assert len(seen) == 1


def test_arrow_keys_move_through_results_and_escape_returns(page, component_origin):
    """Down enters the results, Up and Down move, Escape closes and returns."""
    _answer(page)
    page.goto(component_origin + PATH)
    field, results, _status = _box(page)
    field.fill("examp")
    visible(results.get_by_role("link").first)
    links = results.get_by_role("link")
    field.press("ArrowDown")
    assert links.nth(0).evaluate("node => node === document.activeElement")
    page.keyboard.press("ArrowDown")
    assert links.nth(1).evaluate("node => node === document.activeElement")
    page.keyboard.press("ArrowUp")
    page.keyboard.press("ArrowUp")
    assert field.evaluate("node => node === document.activeElement")
    page.keyboard.press("ArrowDown")
    page.keyboard.press("Escape")
    hidden(results)
    assert field.evaluate("node => node === document.activeElement")
    # Down brings the same answer back without asking again.
    page.keyboard.press("ArrowDown")
    visible(results)


def test_enter_searches_at_once_and_says_when_nothing_matches(page, component_origin):
    """Enter does not wait for the pause; no match is said, not left blank."""
    seen = _answer(page)
    page.goto(component_origin + PATH)
    field, results, status = _box(page)
    field.fill("nobody")
    field.press("Enter")
    recorded(page, seen, 1)
    has_text(
        status,
        "No Family matches. Try part of the surname, a head's "
        "first name, the DUID or the street.",
    )
    visible(results)
    assert results.get_by_role("link").count() == 0
    # Clearing the field closes the results.
    field.fill("")
    hidden(results)


def test_a_refused_search_says_so_in_fixed_words(page, component_origin):
    """A refusal answers with no body; the box words it itself."""
    _answer(page, status=403)
    page.goto(component_origin + PATH)
    field, results, status = _box(page)
    field.fill("examp")
    field.press("Enter")
    has_text(status, "Couldn't search. Refresh the page or sign in again.")
    visible(results)


def test_a_result_opens_its_family_and_tab_leaves_the_box(page, component_origin):
    """Results are ordinary links; leaving the box closes them."""
    _answer(page)
    page.goto(component_origin + PATH)
    field, results, _status = _box(page)
    field.fill("example 3")
    link = results.get_by_role(
        "link", name="Example 1, Anna and Ben DUID 100 · Envelope 500"
    )
    visible(link)
    assert link.get_attribute("href").endswith(
        "/families/00000000-0000-0000-0000-000000000001/"
    )
    page.get_by_role("link", name="Elsewhere").focus()
    hidden(results)


@pytest.mark.parametrize("width", [320, 1280])
def test_the_box_and_its_results_are_accessible(
    page, component_origin, axe_source, width
):
    """No WCAG 2.2 AA violations with results open, and no sideways scroll."""
    _answer(page)
    page.set_viewport_size({"width": width, "height": 900})
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.goto(component_origin + PATH)
    field, results, _status = _box(page)
    field.fill("examp")
    visible(results.get_by_role("link").first)
    assert not failures
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    # The results stay inside the window, beside or under the field.
    box = results.bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= width
    page.evaluate(axe_source)
    violations = page.evaluate("""async () => (await axe.run(document, {
        runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa']}
    })).violations.map(({id, impact, nodes}) => ({
        id, impact, targets: nodes.map(n => n.target)
    }))""")
    assert violations == []


def test_a_late_answer_never_replaces_the_shown_one(page, component_origin):
    """Back to the shown text: the older search in flight is dropped (#669)."""
    seen = _answer(page, hold=("example 3",))
    page.goto(component_origin + PATH)
    field, results, status = _box(page)
    field.fill("examp")
    has_text(status, "12 Families found")
    field.fill("example 3")
    field.press("Enter")
    recorded(page, seen, 2)
    field.fill("examp")
    field.press("Enter")
    _late(seen)
    page.wait_for_timeout(600)
    has_text(results.locator("[data-find-family-summary]"), "12 Families found")
    assert results.get_by_role("link").count() == 8


def test_after_a_failure_the_same_text_searches_again(page, component_origin):
    """A failed answer is never kept as the shown one."""
    seen = _answer(page, statuses=[503])
    page.goto(component_origin + PATH)
    field, results, status = _box(page)
    field.fill("examp")
    field.press("Enter")
    has_text(status, "Find a Family is unavailable right now. Try again in a moment.")
    field.press("Enter")
    recorded(page, seen, 2)
    has_text(status, "12 Families found")
    visible(results.get_by_role("link").first)


def test_leaving_the_box_stops_the_search(page, component_origin):
    """Nothing is sent, or opened over the page, once focus has moved on."""
    seen = _answer(page, hold=("examp",))
    page.goto(component_origin + PATH)
    field, results, _status = _box(page)
    elsewhere = page.get_by_role("link", name="Elsewhere")
    # Before the pause ends: no request at all.
    field.fill("examp")
    elsewhere.focus()
    page.wait_for_timeout(700)
    assert seen == []
    # In flight: the late answer does not open.
    field.focus()
    field.press("Enter")
    recorded(page, seen, 1)
    elsewhere.focus()
    _late(seen)
    page.wait_for_timeout(600)
    hidden(results)
    # Back in the box, Enter searches again and shows the answer.
    _answer(page)
    field.focus()
    field.press("Enter")
    visible(results.get_by_role("link").first)


def test_searching_never_reloads_and_see_all_posts_to_the_directory(
    page, component_origin
):
    """The page stays put while searching; See all is a real form POST."""
    _answer(page)
    page.goto(component_origin + PATH)
    page.evaluate("window.findFamilyMark = 'kept'")
    field, results, _status = _box(page)
    field.fill("examp")
    see_all = results.get_by_role(
        "button", name="See all 12 in the active parishioner family directory"
    )
    visible(see_all)
    assert page.evaluate("window.findFamilyMark") == "kept"
    assert page.url == component_origin + PATH
    with page.expect_request(component_origin + DIRECTORY) as posted:
        see_all.click()
    request = posted.value
    assert request.method == "POST"
    assert parse_qs(request.post_data)["search"] == ["examp"]
    visible(page.get_by_role("heading", name="Active parishioner family directory"))


def test_a_page_restored_from_history_closes_the_results(page, component_origin):
    """A back/forward-cache restore never shows the old answer open."""
    _answer(page)
    page.goto(component_origin + PATH)
    field, results, _status = _box(page)
    field.fill("examp")
    visible(results)
    page.evaluate(
        "window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted: true}))"
    )
    hidden(results)


def test_the_header_fits_a_tablet_with_a_long_parish_name(page, component_origin):
    """Name, box and pills wrap inside a 768 px window; results stay inside."""
    _answer(page)
    page.set_viewport_size({"width": 768, "height": 900})
    page.goto(component_origin + LONG)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    field, results, _status = _box(page)
    box = field.bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= 768 and box["width"] >= 200
    field.fill("examp")
    visible(results)
    shown = results.bounding_box()
    assert shown["x"] >= 0 and shown["x"] + shown["width"] <= 768
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def test_editing_drops_the_answer_still_in_flight(page, component_origin):
    """Type, then edit within the pause: the older answer never shows (#669)."""
    seen = _answer(page, hold=("example 3",))
    page.goto(component_origin + PATH)
    field, results, status = _box(page)
    field.fill("example 3")
    field.press("Enter")
    recorded(page, seen, 1)
    # Edit within the 300 ms pause, then let the older answer land.
    field.press_sequentially("x")
    _late(seen)
    page.wait_for_timeout(250)
    assert "1 Family found" not in (results.text_content() or "")
    assert "1 Family found" not in (status.text_content() or "")
    assert results.get_by_role("link").count() == 0
    # The edited text's own search follows after the pause.
    recorded(page, seen, 2)
    assert seen[1][2]["search"] == ["example 3x"]
    has_text(
        status,
        "No Family matches. Try part of the surname, a head's "
        "first name, the DUID or the street.",
    )
