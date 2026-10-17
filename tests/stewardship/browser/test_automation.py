"""Admin automation pages in Chromium and WebKit (ADM-11).

Automation access opens on its live sessions; its "Include ended sessions"
box and both tables' sort headings refresh the page in place (#621).
Revoking a session there is a row action (#879): the row's Revoke icon asks
in the shared confirmation dialog, then one POST and both tables redraw in
place. Acknowledging the dashboard's automation notices acts in place
(#559). Either way: no reload (a mark outside the regions survives), the
reader's place kept and the change announced; an acknowledgement also sets
the address to the redirect's page. Acknowledging the last notice still
swaps, because the notices region is rendered even when empty. The approval
page's controls never wait for a sign-in (#1547), its review states what was
asked in plain words, and LOCAL's sign-in page takes its link's token.
"""

import pytest

from .automation_components import ACCESS, APPROVAL, HOME, LOCAL_SIGN_IN, REVOKE
from .waits import eventually, has_text, hidden, visible


def contains(locator, text):
    """Assert that ``locator`` comes to contain ``text``, waiting."""
    from playwright.sync_api import expect

    expect(locator).to_contain_text(text)


pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "webkit"], indirect=True
)

MARK = "document.querySelector('h1').dataset.mark = 'kept'"
MARKED = "document.querySelector('h1').dataset.mark"
AXE = """async () => (await axe.run(document, {
    runOnly: {type: 'tag', values: ['wcag2a','wcag2aa','wcag21aa','wcag22aa']}
})).violations.map(({id,impact}) => ({id,impact}))"""


def posts_to(page, part):
    """Collect every POST whose URL contains ``part``."""
    seen = []
    page.on(
        "request",
        lambda request: (
            seen.append(request.url)
            if request.method == "POST" and part in request.url
            else None
        ),
    )
    return seen


@pytest.mark.parametrize(
    "path",
    [
        ACCESS,
        ACCESS + "?ended=yes",
        APPROVAL,
        LOCAL_SIGN_IN,
        "/automation-approval-review",
    ],
)
def test_automation_pages_are_accessible(page, component_origin, axe_source, path):
    """No WCAG 2.2 AA violations, and no sideways scrolling on a phone."""
    page.set_viewport_size({"width": 320, "height": 900})
    page.goto(component_origin + path)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.evaluate(axe_source)
    assert page.evaluate(AXE) == []


def test_wide_session_tables_scroll_inside_their_regions(page, component_origin):
    """A table wider than a phone scrolls in its own region, never the page.

    Fonts differ between machines (CI's Linux fonts are wider than macOS's),
    so the tables are widened on purpose rather than relying on how close
    to the edge they happen to fit.
    """
    page.set_viewport_size({"width": 320, "height": 900})
    page.goto(component_origin + ACCESS + "?ended=yes")
    page.evaluate(
        "document.querySelectorAll('table').forEach(t => t.style.fontSize = '28px')"
    )
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    for region in ("live-table", "ended-table"):
        scroller = page.locator(f"#{region} .table-scroll")
        assert scroller.evaluate("e => e.scrollWidth > e.clientWidth")


def test_live_sessions_are_the_first_table(page, component_origin):
    """The page opens on live sessions; ended ones wait for the box (#621)."""
    page.goto(component_origin + ACCESS)
    first = page.locator("table").first
    assert first.locator("caption").inner_text() == (
        "Sessions that can act as an Administrator now"
    )
    section = first.locator("xpath=ancestor::section[1]")
    assert section.locator(":scope > h2").inner_text() == "Live sessions"
    # Nothing on the page (pairing, approval) comes before it.
    assert page.locator("h1 ~ :is(section, div, p)").first.locator(
        ":scope > h2"
    ).inner_text() == ("Live sessions")
    assert page.locator("table").count() == 1
    assert page.locator("#ended-table").inner_text().strip() == ""
    assert not page.get_by_label("Include ended sessions").is_checked()


def _labels(page, region):
    """The label column of a region's table, in the order shown."""
    cells = page.locator(f"#{region} tbody tr")
    column = 1 if region == "live-table" else 0
    return [row.locator("th, td").nth(column).inner_text() for row in cells.all()]


def test_the_box_shows_and_hides_ended_sessions_in_place(page, component_origin):
    """Ticking the box adds the ended table without a reload, focus stays on
    the box and the address follows; unticking takes the table away again."""
    page.set_viewport_size({"width": 1280, "height": 500})
    page.goto(component_origin + ACCESS)
    page.evaluate(MARK)
    box = page.get_by_label("Include ended sessions")
    box.check()
    contains(page.locator("#ended-table"), "Your ended sessions")
    assert _labels(page, "ended-table") == ["Zeta task", "old job"]
    assert page.locator("#ended-table").get_by_role("button").count() == 0
    assert page.evaluate(MARKED) == "kept"
    assert page.evaluate("document.activeElement.id") == "include-ended"
    has_text(
        page.get_by_role("status").filter(has_text="Ended sessions shown."),
        "Ended sessions shown.",
    )
    assert page.url == component_origin + ACCESS + "?ended=yes#ended-table"
    box.uncheck()
    from playwright.sync_api import expect

    expect(page.locator("#ended-table table")).to_have_count(0)
    has_text(
        page.get_by_role("status").filter(has_text="Ended sessions hidden."),
        "Ended sessions hidden.",
    )
    assert page.evaluate(MARKED) == "kept"
    assert page.url == component_origin + ACCESS + "#ended-table"
    assert not box.is_checked()


def test_headings_sort_both_tables_in_place(page, component_origin):
    """Each table sorts by its own headings, keeping the other's order and the
    box; the sorted heading says so and keeps focus."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + ACCESS + "?ended=yes")
    page.evaluate(MARK)
    assert _labels(page, "live-table") == ["other job", "launch <assistant>"]
    heading = page.locator('#live-table th[data-sort-column="administrator"]')
    heading.locator(".sort-link").click()
    contains(page.locator("#live-table"), "launch <assistant>")
    from playwright.sync_api import expect

    expect(
        page.locator('#live-table th[data-sort-column="administrator"]')
    ).to_have_attribute("aria-sort", "ascending")
    assert _labels(page, "live-table") == ["launch <assistant>", "other job"]
    assert page.evaluate("document.activeElement.closest('th').dataset.sortColumn") == (
        "administrator"
    )
    # The ended table keeps its rows, and the box stays ticked.
    assert _labels(page, "ended-table") == ["Zeta task", "old job"]
    label = page.locator('#ended-table th[data-sort-column="label"] .sort-link')
    label.click()
    expect(page.locator('#ended-table th[data-sort-column="label"]')).to_have_attribute(
        "aria-sort", "ascending"
    )
    assert _labels(page, "ended-table") == ["old job", "Zeta task"]
    # The live table kept its own order.
    assert _labels(page, "live-table") == ["launch <assistant>", "other job"]
    assert page.get_by_label("Include ended sessions").is_checked()
    assert page.evaluate(MARKED) == "kept"
    assert "live_sort=administrator" in page.url and "ended_sort=label" in page.url
    # The box's form follows the headings: its hidden sorts are the new ones.
    hidden = page.locator("#automation-filter input[type=hidden]")
    assert sorted(
        hidden.evaluate_all("els => els.map(e => e.name + '=' + e.value)")
    ) == [
        "ended_sort=label",
        "live_sort=administrator",
    ]


RACE = ACCESS + "?live_sort=label"


def _settled(page, shown):
    """The box and the ended region agree on ``shown``, and stay agreed.

    Each race below ends with a request the page itself sends; waiting for
    the address to carry the region's fragment and the agreed state, then a
    moment more, shows nothing is left to change them.
    """
    from playwright.sync_api import expect

    box = page.get_by_label("Include ended sessions")
    table = page.locator("#ended-table table")
    expect(table).to_have_count(1 if shown else 0, timeout=10_000)
    expect(box).to_be_checked(checked=shown)
    page.wait_for_timeout(2000)
    expect(table).to_have_count(1 if shown else 0)
    expect(box).to_be_checked(checked=shown)
    applied = box.get_attribute("data-applied")
    assert applied == ("true" if shown else "false")


def gets_to(page, part):
    """Collect every GET whose URL contains ``part``, in order."""
    seen = []
    page.on(
        "request",
        lambda request: (
            seen.append(request.url)
            if request.method == "GET" and part in request.url
            else None
        ),
    )
    return seen


def test_unticking_before_the_answer_leaves_the_box_and_page_agreed(
    page, component_origin
):
    """Tick, then untick before the slow answer arrives: the ticked page
    arrives first, then the box sends its unticked state, and no ended table
    is left."""
    page.goto(component_origin + RACE)
    page.evaluate(MARK)
    gets = gets_to(page, ACCESS + "?")
    box = page.get_by_label("Include ended sessions")
    with page.expect_response(lambda response: "ended=yes" in response.url):
        box.check()
        box.uncheck()
    _settled(page, False)
    assert page.evaluate(MARKED) == "kept"
    # The race really happened: the slow ticked request, then the unticked one.
    assert ["ended=yes" in url for url in gets] == [True, False], gets


def test_a_heading_during_a_tick_keeps_the_tick(page, component_origin):
    """A sort chosen while the box's slow request runs supersedes it; the box
    then applies itself again, on the new sort."""
    page.goto(component_origin + RACE)
    page.evaluate(MARK)
    page.get_by_label("Include ended sessions").check()
    page.locator('#live-table th[data-sort-column="administrator"] .sort-link').click()
    _settled(page, True)
    from playwright.sync_api import expect

    expect(
        page.locator('#live-table th[data-sort-column="administrator"]')
    ).to_have_attribute("aria-sort", "ascending")
    assert page.evaluate(MARKED) == "kept"


@pytest.mark.parametrize("answer", ["none", "refused"])
def test_a_tick_the_server_cannot_answer_is_tried_once(page, component_origin, answer):
    """No answer (a restart, offline) or a 400: one in-place request and the
    ordinary load it falls back to, never a loop of resubmissions."""
    page.goto(component_origin + ACCESS)
    ticked = gets_to(page, "ended=yes")

    def answer_route(route):
        """No answer at all, or a refusal."""
        if answer == "none":
            route.abort()
        else:
            route.fulfill(status=400, body="Refused", content_type="text/plain")

    page.route(lambda url: "ended=yes" in url, answer_route)
    page.get_by_label("Include ended sessions").check()
    page.wait_for_timeout(3000)
    assert len(ticked) == 2, ticked


@pytest.mark.parametrize("query", ["", "?ended=yes"])
def test_revoking_a_session_asks_then_acts_in_place(page, component_origin, query):
    """Revoke asks in the dialog; Cancel changes nothing; confirming posts the
    one session, the session lists redraw, the mark and scroll stay, and it
    is said. With ended sessions shown, the revoked session moves to them
    (#621)."""
    from urllib.parse import parse_qs

    page.set_viewport_size({"width": 1280, "height": 400})
    page.goto(component_origin + ACCESS + query)
    page.evaluate(MARK)
    live = page.locator("#live-table")
    # The label is shown as text, never interpreted as markup.
    assert live.locator("tbody").get_by_text("launch <assistant>").count() == 1
    row = live.locator("tr").filter(has_text="launch <assistant>")
    button = row.get_by_role("button", name="Revoke launch <assistant>")
    assert button.get_attribute("title") == "Revoke launch <assistant>"
    # Read the id now: confirming redraws the table and removes this row, so
    # a lazy read afterward would wait for a button that is gone (#1387).
    session_id = button.get_attribute("value")
    # Actions is the last column (#932).
    assert live.locator("thead th").last.inner_text() == "Actions"
    button.scroll_into_view_if_needed()
    offset = page.evaluate("window.scrollY")
    posts = posts_to(page, REVOKE)
    dialog = page.locator("dialog#session-revoke")
    button.click()
    visible(dialog)
    assert dialog.locator("h2").inner_text() == "Revoke launch <assistant>?"
    assert page.evaluate("document.activeElement.textContent.trim()") == "Cancel"
    page.keyboard.press("Escape")
    hidden(dialog)
    assert posts == []
    assert live.locator("tbody").get_by_text("launch <assistant>").count() == 1
    button.click()
    with page.expect_request(lambda request: request.method == "POST") as sent:
        dialog.get_by_role("button", name="Revoke", exact=True).click()
    fields = parse_qs(sent.value.post_data or "")
    assert fields["session_id"] == [session_id]
    assert set(fields) == {"csrfmiddlewaretoken", "session_id"}
    has_text(
        page.get_by_role("status").filter(has_text="Revoked launch <assistant>."),
        "Revoked launch <assistant>.",
    )
    hidden(dialog)
    assert page.evaluate(MARKED) == "kept"
    # No jump to the top: the page scrolls only as far as the live table's
    # heading, which takes focus because the row is gone.
    assert offset > 0 and page.evaluate("window.scrollY") > 0
    body = page.locator("#live-table tbody")
    assert body.get_by_text("other job").count() == 1
    assert body.get_by_text("launch <assistant>").count() == 0
    eventually(page, "document.activeElement.id === 'automation-live-heading'")
    heading = page.locator("#automation-live-heading").bounding_box()
    assert -1 <= heading["y"] <= 401 - heading["height"], heading
    ended = page.locator("#ended-table")
    if query:
        contains(ended, "Revoked by you")
        assert ended.get_by_text("launch <assistant>").count() == 1
    else:
        assert ended.inner_text().strip() == ""
    assert posts == [component_origin + REVOKE + query]


def test_acknowledging_the_last_notice_acts_in_place(page, component_origin):
    """The region empties in place; nothing reloads."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + HOME)
    page.evaluate(MARK)
    region = page.locator("#automation-notices")
    contains(region, "An automation session was approved")
    assert region.get_by_text("launch <assistant>").count() == 1
    region.get_by_role("button", name="Acknowledge", exact=True).click()
    has_text(
        page.get_by_role("status").filter(has_text="Notice acknowledged."),
        "Notice acknowledged.",
    )
    assert page.locator("#automation-notices").inner_text().strip() == ""
    assert page.evaluate(MARKED) == "kept"


def test_acknowledge_all_acts_in_place_and_notices_read_plainly(page, component_origin):
    """Every notice is a plain sentence; Acknowledge all clears them in place."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + HOME + "?two=1")
    page.evaluate(MARK)
    region = page.locator("#automation-notices")
    contains(region, "A command was turned away")
    text = region.inner_text()
    assert "Use refused" not in text and "admin_cmd" not in text
    region.get_by_role("button", name="Acknowledge all").click()
    has_text(
        page.get_by_role("status").filter(has_text="Notices acknowledged."),
        "Notices acknowledged.",
    )
    assert page.locator("#automation-notices").inner_text().strip() == ""
    assert page.evaluate(MARKED) == "kept"


@pytest.mark.parametrize("width", [320, 1280])
def test_approval_never_waits_for_a_sign_in(page, component_origin, width):
    """Whenever the Administrator signed in (#1547), Approve a session is an
    ordinary link to the approval page, and the code field and Continue are
    ready, Continue waiting only for a code."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + ACCESS)
    approve = page.get_by_role("link", name="Approve a session")
    visible(approve)
    assert approve.get_attribute("href") == APPROVAL
    page.goto(component_origin + APPROVAL)
    code = page.get_by_role("textbox", name="User code")
    assert code.is_enabled()
    assert code.get_attribute("aria-describedby") is None
    code.fill("ABCD-EFGH")
    assert page.get_by_role("button", name="Continue").is_enabled()


def test_the_local_sign_in_takes_the_token_from_its_link(page, component_origin):
    """The laptop command's link carries the token in the fragment: the page
    moves it into the form, enables Sign in and drops the fragment; without
    one, Sign in stays unavailable with how to get a link."""
    page.goto(component_origin + LOCAL_SIGN_IN)
    assert page.get_by_role("button", name="Sign in").is_disabled()
    visible(page.locator("#local-sign-in-missing"))
    # A new document, as the opened link is, not a fragment-only change.
    page.goto("about:blank")
    page.goto(component_origin + LOCAL_SIGN_IN + "#" + "t" * 43)
    assert page.locator("#local-sign-in-token").input_value() == "t" * 43
    assert page.get_by_role("button", name="Sign in").is_enabled()
    hidden(page.locator("#local-sign-in-missing"))
    assert page.evaluate("location.hash") == ""


def test_the_review_states_the_request_plainly(page, component_origin):
    """Scope as words, the host prefix and the confused-deputy warning."""
    page.goto(component_origin + "/automation-approval-review")
    review = page.get_by_role("region", name="Review the request")
    contains(review, "Approve only a code you started in your own terminal")
    assert review.get_by_text("launch <assistant>").count() == 1
    assert review.locator("dd").filter(has_text="Full").count() == 1
    assert review.locator("code").filter(has_text="0123456789ab").count() == 1
    assert page.get_by_role("button", name="Approve").is_enabled()
