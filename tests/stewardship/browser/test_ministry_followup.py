"""Native private Ministry follow-up queue, edit form and bulk assignment."""

import pytest

from .followup_components import ASSIGN, ASSIGNED
from .waits import eventually, recorded, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

PAGES = (
    "/followup-queue",
    "/followup-all",
    "/followup-empty",
    "/followup-gated",
    "/followup-item",
    "/followup-closed",
    "/followup-item-stale",
    "/followup-item-gated",
    "/followup-error-400",
    "/followup-error-409",
    "/followup-error-503",
)


@pytest.mark.parametrize("width", [320, 1280])
def test_followup_mobile_keyboard_and_accessibility(
    page, component_origin, axe_source, width
):
    """Every state stays readable, escaped and operable without a JavaScript UI."""
    page.set_viewport_size({"width": width, "height": 900})
    for path in PAGES:
        page.goto(component_origin + path)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.evaluate(axe_source)
        assert (
            page.evaluate("""async () => (await axe.run(document, {
            runOnly: {type: 'tag', values: ['wcag2a','wcag2aa','wcag21aa','wcag22aa']}
        })).violations.map(({id,impact}) => ({id,impact}))""")
            == []
        )
    page.goto(component_origin + "/followup-queue")
    assert page.get_by_text("Example <Member>", exact=True).count() == 1
    visible(page.get_by_label("Select Example <Member>"))
    search = page.get_by_label("Search Member or Ministry name")
    search.focus()
    page.keyboard.press("Tab")
    assert page.locator(":focus").get_attribute("name") == "ministry"
    # Bulk assignment needs one Ministry's assignees; otherwise it explains why.
    page.goto(component_origin + "/followup-all")
    assert page.get_by_role("button", name="Assign selected").count() == 0
    visible(page.get_by_text("Choose one Ministry above", exact=False))
    page.goto(component_origin + "/followup-item")
    assert page.get_by_text("Left a <private> voicemail", exact=True).count() == 2
    assert page.get_by_text("No <answer>", exact=False).count() == 1
    assert page.get_by_label("Assigned to").input_value() != ""
    # A revoked assignee is named, never silently dropped to "Unassigned".
    page.goto(component_origin + "/followup-item-stale")
    notice = page.get_by_text("can no longer follow up this Ministry", exact=False)
    visible(notice)
    assert "leader@example.org" in notice.inner_text()
    assert page.get_by_text("New and Assigned follow the assignee", exact=False).count()
    # Closed outcomes are permanent: history remains, the form does not.
    page.goto(component_origin + "/followup-closed")
    assert page.get_by_role("button", name="Save follow-up").count() == 0
    visible(page.get_by_text("Closed outcomes are permanent", exact=False))
    for path, control in (
        ("/followup-item-gated", "Save follow-up"),
        ("/followup-gated", "Assign selected"),
    ):
        page.goto(component_origin + path)
        assert page.get_by_role("button", name=control).is_disabled()


def test_followup_filters_without_scripts(browser_engine, component_origin):
    """Identifying filters submit only through native POST, never the URL."""
    context = browser_engine.new_context(java_script_enabled=False)
    try:
        page = context.new_page()
        page.goto(component_origin + "/followup-queue")
        page.get_by_label("Search Member or Ministry name").fill("Private name")
        page.get_by_label("Assigned to").first.select_option("mine")
        page.route("**/follow-up/", lambda route: route.fulfill(body="Filtered"))
        with page.expect_request(lambda request: request.method == "POST") as sent:
            page.get_by_role("button", name="Apply filters").click()
        assert "search=Private+name" in sent.value.post_data
        assert "assignee=mine" in sent.value.post_data
        assert "ministry=9" in sent.value.post_data
        assert "Private" not in sent.value.url and "?" not in sent.value.url
    finally:
        context.close()


def test_followup_edit_and_bulk_assignment_without_scripts(
    browser_engine, component_origin
):
    """The edit and the exact versioned selection post natively with no URL state."""
    context = browser_engine.new_context(java_script_enabled=False)
    try:
        page = context.new_page()
        page.goto(component_origin + "/followup-item")
        page.get_by_label("Status").select_option("resolved")
        page.get_by_label("Outcome, required when resolving or closing").select_option(
            "joined"
        )
        page.get_by_label("How").select_option("email")
        page.get_by_label("Date (UTC)").fill("2026-09-19")
        page.get_by_label("Time (UTC)").fill("15:04")
        page.get_by_label("What happened").fill("Private reply")
        page.route("**/update", lambda route: route.fulfill(body="Saved"))
        with page.expect_request(lambda request: request.method == "POST") as sent:
            page.get_by_role("button", name="Save follow-up").click()
        body = sent.value.post_data
        assert "state=resolved" in body and "outcome=joined" in body
        assert "expected_version=3" in body and "request_key=" in body
        assert "contact_channel=email" in body and "contact_date=2026-09-19" in body
        assert "Private" not in sent.value.url and "?" not in sent.value.url

        page.goto(component_origin + "/followup-queue")
        page.get_by_label("Select Example <Member>").check()
        page.get_by_label("Assign to").select_option(label="leader@example.org")
        page.route("**/assign", lambda route: route.fulfill(body="Assigned"))
        with page.expect_request(lambda request: request.method == "POST") as sent:
            page.get_by_role("button", name="Assign selected").click()
        body = sent.value.post_data
        # The selection binds each request to the version this page displayed.
        assert "selected=00000000-0000-0000-0000-00000000005d%3A3" in body
        assert "ministry=9" in body and "assignee=00000000" in body
        # The queue's private view rides along, so the queue keeps it (#518).
        assert "queue-search=Example" in body and "queue-ministry=9" in body
        assert "queue-sort=newest" in body and "queue-page=1" in body
        assert "?" not in sent.value.url
    finally:
        context.close()


def test_followup_sort_heading_without_scripts(browser_engine, component_origin):
    """A heading re-sorts the queue by native POST, keeping private filters."""
    context = browser_engine.new_context(java_script_enabled=False)
    try:
        page = context.new_page()
        page.goto(component_origin + "/followup-queue")
        heading = page.get_by_role("columnheader", name="Request")
        assert heading.get_attribute("aria-sort") == "descending"
        page.route("**/follow-up/", lambda route: route.fulfill(body="Sorted"))
        with page.expect_request(lambda request: request.method == "POST") as sent:
            page.get_by_role("columnheader", name="Member").get_by_role(
                "button"
            ).click()
        body = sent.value.post_data
        assert "sort=name" in body and "search=Example" in body
        assert "ministry=9" in body and "?" not in sent.value.url
    finally:
        context.close()


# A mark outside every region, which only a full page load could lose.
MARK = "document.querySelector('h1').dataset.mark = 'kept'"
MARKED = "document.querySelector('h1').dataset.mark"


def record_assignments(page):
    """The bodies of the bulk-assignment POSTs the page sends.

    The fixture server answers each as the view does, with a 302 back to the
    filtered queue (followup_components.ASSIGN).
    """
    posts = []
    page.on(
        "request",
        lambda request: (
            posts.append(request.post_data)
            if request.method == "POST" and request.url.endswith(ASSIGN)
            else None
        ),
    )
    return posts


def test_bulk_assignment_keeps_filters_place_and_focus(page, component_origin):
    """Assign selected refreshes the queue in place: one POST, the private
    filters kept, the reader's scroll and focus kept, the selection cleared."""
    page.set_viewport_size({"width": 1280, "height": 500})
    page.goto(component_origin + "/followup-queue")
    page.evaluate(MARK)
    posts = record_assignments(page)
    page.get_by_label("Select Example <Member>").check()
    page.get_by_label("Assign to").select_option(label="leader@example.org")
    button = page.get_by_role("button", name="Assign selected")
    button.scroll_into_view_if_needed()
    offset = page.evaluate("window.scrollY")
    assert offset > 0, "the button must sit below the fold for this test"
    button.click()
    visible(page.locator("#table").get_by_text("other@example.org"))
    # The live region says what happened, then the rows now shown.
    status = page.get_by_role("status").filter(has_text="Assignment saved.")
    visible(status)
    assert status.inner_text().startswith("Assignment saved. Showing ")
    assert page.evaluate(MARKED) == "kept"
    assert abs(page.evaluate("window.scrollY") - offset) < 5
    eventually(page, "document.activeElement.id", "followup-assign-submit")
    # The filters the assignment was made under are still the queue's.
    assert page.get_by_label("Search Member or Ministry name").input_value() == (
        "Example"
    )
    assert page.get_by_label("Ministry", exact=True).input_value() == "9"
    # The reassigned row carries its next version, unticked; the new form
    # carries the view again for the next assignment.
    box = page.get_by_label("Select Example <Member>")
    assert box.get_attribute("value").endswith(":4") and not box.is_checked()
    assert page.locator('#followup-assign [name="queue-search"]').input_value() == (
        "Example"
    )
    # The "Assign to" choice is kept for the next assignment.
    assert page.get_by_label("Assign to").input_value() == (
        "00000000-0000-0000-0000-00000000005e"
    )
    assert len(posts) == 1
    assert "queue-search=Example" in posts[0] and "selected=" in posts[0]
    # A POST table's address never takes a private value.
    assert "Example" not in page.url and "?" not in page.url


def test_repeated_assign_click_sends_one_post(page, component_origin):
    """A double click on Assign selected sends the assignment once."""
    page.goto(component_origin + "/followup-queue")
    posts = record_assignments(page)
    page.get_by_label("Select Example <Member>").check()
    page.get_by_role("button", name="Assign selected").dblclick()
    visible(page.locator("#table").get_by_text("other@example.org"))
    eventually(page, "document.activeElement.id", "followup-assign-submit")
    assert len(posts) == 1


def test_stale_assignment_answer_is_shown_without_resending(page, component_origin):
    """A 409 (a request changed since the page loaded) is shown as returned,
    and the assignment is not sent again."""
    page.goto(component_origin + "/followup-queue")
    posts = []

    def handle(route):
        posts.append(route.request.post_data)
        stale = route.fetch(url=component_origin + "/followup-error-409", method="GET")
        route.fulfill(response=stale, status=409)

    page.route("**/follow-up/assign", handle)
    page.get_by_label("Select Example <Member>").check()
    page.get_by_role("button", name="Assign selected").click()
    visible(page.get_by_text("This request changed. Nothing was saved."))
    assert page.locator("#table").count() == 0
    assert len(posts) == 1


def test_bulk_assignment_without_scripts_returns_to_filtered_queue(
    browser_engine, component_origin
):
    """No script: the native POST carries the view and its redirect lands on
    the filtered queue at the table, with no private value in the URL."""
    context = browser_engine.new_context(java_script_enabled=False)
    try:
        page = context.new_page()
        page.goto(component_origin + "/followup-queue")
        posts = record_assignments(page)
        page.get_by_label("Select Example <Member>").check()
        page.get_by_role("button", name="Assign selected").click()
        page.wait_for_url(component_origin + ASSIGNED)
        # The redirect names the table as its fragment, and the page it lands
        # on has that target, so the browser can bring the reader to it.
        # Where the viewport ends up is the engine's own fragment handling,
        # which varies: on Linux CI, WebKit has both kept the earlier scroll
        # position and shown the top, so no scroll position is asserted.
        assert page.url.endswith("#table")
        assert page.locator("#table[data-table-region]").count() == 1
        assert len(posts) == 1 and "queue-search=Example" in posts[0]
        assert page.get_by_label("Search Member or Ministry name").input_value() == (
            "Example"
        )
        assert page.get_by_label("Ministry", exact=True).input_value() == "9"
        visible(page.locator("#table").get_by_text("other@example.org"))
    finally:
        context.close()


def test_table_controls_wait_while_an_assignment_is_saved(page, component_origin):
    """A heading chosen while Assign selected is in flight is ignored, so the
    assignment is never aborted: one POST, swapped rows, the announcement."""
    page.goto(component_origin + "/followup-queue")
    posts, held, sorts = record_assignments(page), [], []
    page.route("**/follow-up/assign", lambda route: held.append(route))
    page.on(
        "request",
        lambda request: (
            sorts.append(request.url)
            if request.method == "POST" and request.url.endswith("/follow-up/")
            else None
        ),
    )
    page.get_by_label("Select Example <Member>").check()
    page.get_by_role("button", name="Assign selected").click()
    recorded(page, held, 1)
    page.get_by_role("columnheader", name="Member").get_by_role("button").click()
    page.wait_for_timeout(300)
    held[0].continue_()
    visible(page.locator("#table").get_by_text("other@example.org"))
    visible(page.get_by_role("status").filter(has_text="Assignment saved."))
    assert len(posts) == 1 and sorts == []
