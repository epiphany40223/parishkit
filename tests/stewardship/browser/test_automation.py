"""Admin automation pages in Chromium and WebKit (ADM-11).

Automation access opens on its live sessions; its "Include ended sessions"
box and both tables' sort headings refresh the page in place (#621).
Revoking a session there and acknowledging the dashboard's
automation notices act in place (#559): one POST, no reload (a mark outside
the regions survives), the reader's place kept, the change announced, and
the address set to the redirect's page. Acknowledging the last notice still
swaps, because the notices region is rendered even when empty. The approval
page offers "Confirm with Google" with its controls unavailable while the
sign-in is stale, and its review states what was asked in plain words.
"""

import pytest

from .automation_components import ACCESS, APPROVAL, HOME, LOCAL_SIGN_IN, REVOKE
from .waits import has_text


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
        "/automation-access-stale",
        "/automation-access-stale-local",
        APPROVAL,
        "/automation-approval-stale-local",
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
    assert section.locator("h2").inner_text() == "Live sessions"
    # Nothing on the page (pairing, approval) comes before it.
    assert page.locator("h1 ~ :is(section, div, p)").first.locator(
        "h2"
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


def test_a_tick_during_a_revoke_applies_after_the_save(page, component_origin):
    """A tick while Revoke saves is held back ("Still saving…"), then applied."""
    page.goto(component_origin + RACE)
    page.evaluate(MARK)
    row = page.locator("#live-table tr").filter(has_text="launch <assistant>")
    row.get_by_role("button", name="Revoke").click()
    page.get_by_label("Include ended sessions").check()
    has_text(
        page.get_by_role("status").filter(has_text="Still saving"), "Still saving…"
    )
    # Said beside the box too, while the save runs.
    contains(page.locator("#automation-filter"), "Still saving")
    _settled(page, True)
    assert page.locator("#automation-filter [data-held-note]").count() == 0
    # The revoked session stays revoked: gone from the live table, and listed
    # among the ended ones.
    assert page.locator("#live-table").get_by_text("launch <assistant>").count() == 0
    assert _labels(page, "live-table") == ["other job"]
    assert "launch <assistant>" in _labels(page, "ended-table")
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
def test_revoking_a_session_acts_in_place(page, component_origin, query):
    """One POST; the session lists swap; the mark and scroll stay; it is said.

    With ended sessions shown, the revoked session moves to them (#621).
    """
    page.set_viewport_size({"width": 1280, "height": 400})
    page.goto(component_origin + ACCESS + query)
    page.evaluate(MARK)
    live = page.locator("#live-table")
    # The label is shown as text, never interpreted as markup.
    assert live.get_by_text("launch <assistant>").count() == 1
    row = live.locator("tr").filter(has_text="launch <assistant>")
    button = row.get_by_role("button", name="Revoke")
    button.scroll_into_view_if_needed()
    offset = page.evaluate("window.scrollY")
    # The page may shrink by the revoked row's own height, which depends on
    # the platform's fonts (taller on CI's Linux), so measure it.
    shrink = row.evaluate("row => row.getBoundingClientRect().height")
    posts = posts_to(page, REVOKE)
    button.click()
    has_text(
        page.get_by_role("status").filter(has_text="Session revoked."),
        "Session revoked.",
    )
    assert page.evaluate(MARKED) == "kept"
    # No jump to the top; the page may shrink a little under the reader, as
    # the revoked session leaves the list of live ones.
    assert offset > 0 and page.evaluate("window.scrollY") > 0
    assert abs(page.evaluate("window.scrollY") - offset) <= shrink + 16
    assert page.locator("#live-table").get_by_text("other job").count() == 1
    assert page.locator("#live-table").get_by_text("launch <assistant>").count() == 0
    ended = page.locator("#ended-table")
    if query:
        contains(ended, "Revoked by you")
        assert ended.get_by_text("launch <assistant>").count() == 1
    else:
        assert ended.inner_text().strip() == ""
    assert posts == [component_origin + REVOKE + query]
    assert page.url.startswith(
        component_origin + ACCESS + (query + "&" if query else "?") + "revoked=1"
    )


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


def step_up_returns_to(page, path):
    """Assert the visible re-sign-in control posts to the login route with
    ``next`` naming ``path``, so the sign-in comes back to that page."""
    button = page.get_by_role("button", name="Confirm with Google")
    assert button.count() == 1 and button.is_visible() and button.is_enabled()
    form = page.locator("form").filter(has=button)
    assert form.get_attribute("method") == "post"
    assert form.get_attribute("action") == "/admin/login"
    assert form.locator("input[name=next]").get_attribute("value") == path


@pytest.mark.parametrize("width", [320, 1280])
def test_a_stale_sign_in_offers_a_working_step_up(page, component_origin, width):
    """Both stale pages show a visible re-sign-in control that returns to the
    approval page; Continue, the code field and Approve a session wait."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + "/automation-approval-stale")
    step_up_returns_to(page, APPROVAL)
    assert page.get_by_role("button", name="Continue").is_disabled()
    assert page.get_by_role("textbox", name="User code").is_disabled()
    page.goto(component_origin + "/automation-access-stale")
    step_up_returns_to(page, APPROVAL)
    assert page.get_by_role("button", name="Approve a session").is_disabled()


STORED_STEP_UP = "JSON.parse(localStorage.getItem('pk-local-step-up') || 'null')"


@pytest.mark.parametrize(
    "path,says",
    [
        ("/automation-approval-stale-local", "brings you back to this page"),
        ("/automation-access-stale-local", "takes you to the approval page"),
    ],
)
def test_the_local_step_up_returns_to_the_approval_page(
    page, component_origin, path, says
):
    """LOCAL has no Google (#613): the page names the laptop command and where
    the sign-in goes, records the approval page, and the local sign-in sends
    it as "next", clearing it only when the form is submitted."""
    page.goto(component_origin + path)
    assert page.get_by_role("button", name="Confirm with Google").count() == 0
    step_up = page.locator("[data-local-step-up]")
    assert step_up.is_visible()
    contains(step_up, "tools/stewardship-local.sh sign-in --email E")
    contains(step_up, says)
    assert page.evaluate(STORED_STEP_UP)["next"] == APPROVAL
    page.goto(component_origin + LOCAL_SIGN_IN + "#" + "t" * 43)
    assert page.locator("#local-sign-in-next").get_attribute("value") == APPROVAL
    # Opening the page keeps it: a link opened but not submitted keeps it.
    assert page.evaluate(STORED_STEP_UP)["next"] == APPROVAL
    with page.expect_navigation():
        page.get_by_role("button", name="Sign in").click()
    assert page.evaluate(STORED_STEP_UP) is None


def test_an_old_local_step_up_is_not_used(page, component_origin):
    """A return path recorded more than ten minutes ago is dropped."""
    page.goto(component_origin + "/automation-approval-stale-local")
    page.evaluate(
        "localStorage.setItem('pk-local-step-up', JSON.stringify("
        "{next: '/admin/', at: Date.now() - 11 * 60 * 1000}))"
    )
    page.goto(component_origin + LOCAL_SIGN_IN + "#" + "t" * 43)
    assert page.locator("#local-sign-in-next").get_attribute("value") == ""


def test_the_review_states_the_request_plainly(page, component_origin):
    """Scope as words, the host prefix and the confused-deputy warning."""
    page.goto(component_origin + "/automation-approval-review")
    review = page.get_by_role("region", name="Review the request")
    contains(review, "Approve only a code you started in your own terminal")
    assert review.get_by_text("launch <assistant>").count() == 1
    assert review.locator("dd").filter(has_text="Full").count() == 1
    assert review.locator("code").filter(has_text="0123456789ab").count() == 1
    assert page.get_by_role("button", name="Approve").is_enabled()
