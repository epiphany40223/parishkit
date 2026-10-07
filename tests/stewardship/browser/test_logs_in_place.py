"""System logs' cross-links filter in place (#519 PR 3).

Show related entries, Same actor and Same campaign each send their filter in
a POST body, as before. The page is served at the address its forms post
to (log_components.LIVE), so the answer is this same page, and a route
answers each POST with the fixture page the filter leads to. Each test sets
a mark outside every region, which only a full page load could lose.
"""

from uuid import UUID

import pytest

from .log_components import LIVE
from .test_in_place import MARK, MARKED, VIEWPORT, count_requests, scroll_below
from .test_system_logs import posted
from .waits import eventually, has_text, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

AUDIT = UUID(int=300)
# Whether the focused element is inside the viewport.
ON_SCREEN = """() => {
    const box = document.activeElement.getBoundingClientRect();
    return box.top >= 0 && box.bottom <= window.innerHeight;
}"""
# What each filtered answer's navigator says, read after the message.
COUNT = "Showing 1–1 of 1 · Page 1 of 1"
DEBUG = UUID(int=100)
# The filter form's visible fields as the page draws them: each field's
# value, or whether a checkbox is ticked, and whether "Filter by identifier"
# is open.
FILTERS = """(root) => {
    const form = root.getElementById("table-filters");
    const fields = {};
    [...form.elements].forEach((field) => {
        if (!field.name || field.type === "hidden" || field.type === "submit") return;
        fields[field.name] = field.type === "checkbox" ? field.checked : field.value;
    });
    fields.identifiers = form.querySelector("#log-identifier-filters").open;
    return fields;
}"""


def answer_with(page, origin, fixture):
    """Answer every POST to the page's own address with ``fixture``."""
    page.route(
        "**" + LIVE,
        lambda route: (
            route.fulfill(response=route.fetch(url=origin + fixture, method="GET"))
            if route.request.method == "POST"
            else route.continue_()
        ),
    )


def fixture_filters(page, origin, fixture):
    """The filter form's fields as ``fixture`` draws them, read from a
    parsed copy of that page."""
    return page.evaluate(
        f"""async (url) => {{
        const text = await (await fetch(url)).text();
        return ({FILTERS})(new DOMParser().parseFromString(text, "text/html"));
    }}""",
        origin + fixture,
    )


@pytest.mark.parametrize(
    "name, fixture, sent, message, address",
    [
        (
            "related",
            "/logs-related",
            {"correlation": [str(UUID(int=302))]},
            "Related entries shown.",
            "",
        ),
        (
            "campaign",
            "/logs-campaign",
            {"applied": ["yes"], "audit": ["yes"], "campaign": [str(UUID(int=303))]},
            "Audit records for the same campaign shown.",
            "?applied=yes&audit=yes",
        ),
    ],
)
def test_cross_link_filters_in_place(
    page, component_origin, name, fixture, sent, message, address
):
    """A cross-link sends its filter once in a POST body and swaps the list
    in place: no reload, the reader's place kept, the address showing only
    the filters a link may carry (#536, never the identifier), focus on the
    same entry's button in the filtered list, the message and the new count
    announced. The list is shorter, so the
    page can scroll up a little, but never to the top. The filter form then shows the
    filters applied, "Filter by identifier" opened, so the next Apply keeps
    them."""
    page.set_viewport_size(VIEWPORT)
    page.goto(component_origin + LIVE)
    page.evaluate(MARK)
    button = f"#log-audit-{AUDIT}-{name}"
    offset = scroll_below(page, button)
    answer_with(page, component_origin, fixture)
    posts = count_requests(page, "POST", LIVE)
    with page.expect_request(lambda request: request.method == "POST") as request:
        page.locator(button).click()
    assert posted(request.value) == sent
    visible(page.locator("#table").get_by_text(COUNT).first)
    assert page.evaluate(MARKED) == "kept"
    # The list is shorter now, so the page may have to scroll up a little,
    # but never back to the top, and the focused button is on screen.
    assert 0 < page.evaluate("window.scrollY") <= offset
    assert page.evaluate(ON_SCREEN)
    assert page.evaluate("document.activeElement.id") == button[1:]
    has_text(page.get_by_role("status").filter(has_text=message), f"{message} {COUNT}")
    assert page.url == component_origin + LIVE + address
    assert len(posts) == 1
    filters = page.evaluate(f"() => ({FILTERS})(document)")
    assert filters == fixture_filters(page, component_origin, fixture)
    assert filters["identifiers"] is True
    apply = page.get_by_role("button", name="Apply filters")
    assert apply.is_enabled()
    # The next Apply sends the filter the cross-link applied. Wait for its
    # answer too: the route fetches the fixture and fulfils the POST, and a
    # fulfil still in flight when the context closes fails a later test's
    # setup or teardown with "Fetch response has been disposed".
    with page.expect_response(
        lambda response: response.request.method == "POST"
    ) as answer:
        apply.click()
    fields = posted(answer.value.request)
    for key, value in sent.items():
        assert fields[key] == value


def test_cross_link_without_its_entry_focuses_the_list(page, component_origin):
    """When the filtered list no longer shows the entry the cross-link was
    chosen from, focus goes to the list itself, still without a reload."""
    page.set_viewport_size(VIEWPORT)
    page.goto(component_origin + LIVE)
    page.evaluate(MARK)
    scroll_below(page, f"#log-debug-{DEBUG}-actor")
    answer_with(page, component_origin, "/logs-actor")
    page.locator(f"#log-debug-{DEBUG}-actor").click()
    message = "Entries by the same actor shown."
    has_text(page.get_by_role("status").filter(has_text=message), f"{message} {COUNT}")
    assert page.evaluate(MARKED) == "kept"
    assert page.evaluate("document.activeElement.id") == "table"
    assert page.get_by_label("Actor identifier").input_value() == str(UUID(int=900))


def test_sorting_keeps_unapplied_filters(page, component_origin):
    """Only a cross-link rewrites the visible filters: a sort keeps what the
    reader has typed but not applied."""
    page.goto(component_origin + LIVE)
    page.get_by_label("Event or action type").fill("task_failed")
    answer_with(page, component_origin, "/logs-live-oldest")
    page.get_by_role("button", name="sort ascending").click()
    visible(page.locator("#table th[data-sort-column='time'][aria-sort=ascending]"))
    assert page.get_by_label("Event or action type").input_value() == "task_failed"


def test_cross_link_without_an_answer_falls_back_to_the_ordinary_post(
    page, component_origin
):
    """A cross-link only reads, so when its fetch gets no answer at all it is
    submitted the ordinary way (a full load of the filtered page), as a
    table's POST is; a save would never be sent again."""
    page.goto(component_origin + LIVE)
    page.evaluate(MARK)
    sent = []

    def answer(route):
        """Drop the in-place fetch; answer the ordinary submission."""
        if route.request.method != "POST":
            route.continue_()
            return
        sent.append(route.request.post_data)
        if len(sent) == 1:
            route.abort()
        else:
            response = route.fetch(url=component_origin + "/logs-related", method="GET")
            route.fulfill(response=response)

    page.route("**" + LIVE, answer)
    page.locator(f"#log-audit-{AUDIT}-related").click()
    # The ordinary load replaced the page, mark and all.
    eventually(page, "() => document.querySelector('h1')?.dataset.mark ?? null", None)
    visible(page.locator("#table").get_by_text(COUNT).first)
    assert len(sent) == 2 and sent[0] == sent[1]


def test_the_address_follows_applied_filters_without_identifiers(
    page, component_origin
):
    """After an in-place Apply the address bar shows the page's own link
    (#536): the Ministry, never the search text or the correlation
    identifier, so a bookmark or reload keeps what a link may carry. No
    reload happens."""
    page.goto(component_origin + LIVE)
    page.evaluate(MARK)
    answer_with(page, component_origin, "/logs-searched")
    page.locator("#log-text").fill("lag")
    with page.expect_request(lambda request: request.method == "POST") as request:
        page.get_by_role("button", name="Apply filters").click()
    assert posted(request.value)["text"] == ["lag"]
    link = page.get_by_role("link", name="Link to these filters")
    visible(
        page.locator("#log-link").get_by_text("search and identifier filters are left")
    )
    assert page.evaluate(MARKED) == "kept"
    expected = "?applied=yes&info=yes&warning=yes&error=yes&audit=yes&ministry=42"
    eventually(page, "() => window.location.search", expected)
    assert link.get_attribute("href") == LIVE + expected
    assert str(UUID(int=302)) not in page.url and "lag" not in page.url


@pytest.mark.parametrize(("zone", "shown"), [("Asia/Tokyo", False), ("UTC", True)])
def test_a_followed_links_other_zone_is_noted(
    browser_engine, component_origin, zone, shown
):
    """A link's days are in the zone it was made in (#536): the page says so
    only when that is not this browser's zone."""
    context = browser_engine.new_context(timezone_id=zone)
    try:
        page = context.new_page()
        page.goto(component_origin + "/logs-linked-tokyo")
        note = page.locator("[data-link-zone]")
        assert note.count() == 1
        if shown:
            visible(note.get_by_text("days in Asia/Tokyo", exact=False))
        else:
            assert note.is_hidden()
    finally:
        context.close()
