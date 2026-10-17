"""Export Retry and Cancel, and a Mail message's resolutions, act in place
(#519 PR 7c-4); Cancel ends its export, whose answer is then the fixed "no
longer available" page (#1398).

The pages are served at their real addresses (in_place_commands_components),
and the fixture server answers each command with its view's real 303 back to
the same page, so the followed redirect is checked too.

Each test sets a mark outside every region, which only a full page load could
lose, wheels the page so the button is below the fold (the mouse wheel, since
Firefox can undo a scripted scroll, #1088), and checks the mark, that the page
did not jump back to the top, the fresh content, where focus went, the
announcement and that exactly one POST was sent. A refused command (409) is
shown whole, as a native submission shows it, and sent once.
"""

import pytest

from .in_place_commands_components import (
    DELIVERY,
    EXACT_CANCEL,
    EXACT_FAILED,
    EXACT_QUEUED,
    EXACT_RETRY,
    EXPORT_CANCEL,
    EXPORT_FAILED,
    EXPORT_QUEUED,
    EXPORT_REFUSED,
    EXPORT_REFUSED_RETRY,
    EXPORT_RETRY,
    RESOLVE,
    SAVED_NOTE,
)
from .test_in_place import MARK, MARKED, count_requests
from .waits import focused, has_value, hidden, painted, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

# Short enough that every page's command button starts below the fold.
VIEWPORT = {"width": 1280, "height": 320}
# The page's own live region (ui-v1.js), which reads each command's message.
LIVE = "div.visually-hidden[role=status]"


def wheel_to(page, button, *, attempts=20):
    """Wheel the page until ``button`` sits 150 px below the viewport's top,
    or as far as the page scrolls, and holds there; return that scrollY.

    The wheel, not a script: Firefox can re-apply an earlier scroll into view
    (a focused field's caret, after typing a note) and undo a scripted one
    (#1088). A wheel turn can stop short, so the page is wheeled until it
    holds across two settled checks. Positions are measured from the button,
    never from the page's top, since fonts differ between engines and hosts.
    """
    where = (
        "node => Math.min(document.documentElement.scrollHeight"
        " - document.documentElement.clientHeight,"
        " scrollY + node.getBoundingClientRect().top - 150)"
    )

    def there():
        """Whether the page already sits where ``where`` puts the button."""
        return abs(button.evaluate(where) - page.evaluate("scrollY")) <= 1

    # Over the main content, so the wheel scrolls the page, not the menu.
    page.mouse.move(900, 200)
    for _ in range(attempts):
        page.mouse.wheel(0, button.evaluate(where) - page.evaluate("scrollY"))
        painted(page)
        if not there():
            continue
        painted(page)
        if there():
            offset = page.evaluate("scrollY")
            assert offset > 0, "the button must start below the fold for this test"
            return offset
    raise AssertionError("the page did not stay below the button")


def hold_polls(page, origin, address):
    """Leave the status page's own polls (live-status-v1.js re-reads a
    pending export's address) unanswered, so the fixture's unchanging page
    never redraws the panel under the click. The command's POST and the
    redirect's stand-in address are not held."""
    page.route(
        lambda url: url.split("#")[0] == origin + address,
        lambda route: None if route.request.method == "GET" else route.fallback(),
    )


def open_marked(page, origin, address):
    """Open ``address`` in the short viewport and mark it (see MARK)."""
    page.set_viewport_size(VIEWPORT)
    page.goto(origin + address)
    page.evaluate(MARK)


def stayed(page, offset):
    """Check that the page did not reload and did not jump back to the top:
    the mark survives, the page sits within a viewport of where the reader
    was (or as far down as a shorter page still goes), and the focused
    element is in view."""
    from playwright.sync_api import expect  # imported late: collection needs no browser

    expect(page.locator(LIVE)).not_to_have_text("")
    assert page.evaluate(MARKED) == "kept"
    lowest = page.evaluate(
        "document.documentElement.scrollHeight - document.documentElement.clientHeight"
    )
    assert page.evaluate("scrollY") >= min(offset, lowest) - VIEWPORT["height"]
    assert page.evaluate("scrollY") > 0
    assert page.evaluate(
        "() => { const box = document.activeElement.getBoundingClientRect();"
        " return box.bottom > 0 && box.top < document.documentElement.clientHeight; }"
    )


def announced(page, text):
    """Wait until the page's live region reads ``text``."""
    from playwright.sync_api import expect

    expect(page.locator(LIVE)).to_contain_text(text)


@pytest.mark.parametrize(
    "address, post, name, message, shown",
    [
        (
            EXPORT_FAILED,
            EXPORT_RETRY,
            "Retry export",
            "Export retry started.",
            "Your file is ready.",
        ),
        (
            EXACT_FAILED,
            EXACT_RETRY,
            "Retry export",
            "Export retry started.",
            "The export's figures are ready.",
        ),
    ],
)
def test_export_commands_are_in_place(
    page, component_origin, address, post, name, message, shown
):
    """Retry export and Cancel export post once (a double click included),
    follow the view's redirect back and swap the status panel in. The button
    is gone then, so focus falls back to Refresh status just below the panel
    rather than up to the page heading, and the address is the redirect's,
    with the panel's fragment."""
    open_marked(page, component_origin, address)
    hold_polls(page, component_origin, address)
    posts = count_requests(page, "POST", post)
    button = page.get_by_role("button", name=name, exact=True)
    offset = wheel_to(page, button)
    button.dblclick()
    announced(page, message)
    stayed(page, offset)
    visible(page.locator("#export-status").get_by_text(shown, exact=False))
    hidden(page.get_by_role("button", name=name, exact=True))
    focused(page.get_by_role("link", name="Refresh status"))
    assert posts == [component_origin + post]
    assert page.url.startswith(component_origin + address + "?")
    assert page.url.endswith("#export-status")


@pytest.mark.parametrize(
    "address, post", [(EXPORT_QUEUED, EXPORT_CANCEL), (EXACT_QUEUED, EXACT_CANCEL)]
)
def test_cancel_answers_the_no_longer_available_page(
    page, component_origin, address, post
):
    """A cancelled export has ended (#1398): Cancel's 303 lands on the status
    page, which now answers the fixed "no longer available" page (410). It
    is shown whole, as a native submission shows it, with its link back to
    the report, and the POST is sent once."""
    open_marked(page, component_origin, address)
    hold_polls(page, component_origin, address)
    posts = count_requests(page, "POST", post)
    button = page.get_by_role("button", name="Cancel export", exact=True)
    wheel_to(page, button)
    button.click()
    visible(page.get_by_role("heading", name="Export no longer available"))
    visible(page.get_by_text("This export is no longer available."))
    visible(page.get_by_role("link", name="Participation", exact=True).last)
    painted(page)
    assert posts == [component_origin + post]


def test_refused_export_retry_is_shown_once(page, component_origin):
    """A refused Retry (the view's 409 error page, which draws no status
    panel) is shown whole, as a native submission shows it, never swallowed,
    and is not sent again."""
    open_marked(page, component_origin, EXPORT_REFUSED)
    posts = count_requests(page, "POST", EXPORT_REFUSED_RETRY)
    button = page.get_by_role("button", name="Retry export", exact=True)
    wheel_to(page, button)
    button.click()
    visible(page.get_by_role("heading", name="Export action unavailable"))
    visible(page.get_by_text("The export request could not be completed."))
    painted(page)
    assert posts == [component_origin + EXPORT_REFUSED_RETRY]


# Each resolution: its button, its message, the email's state line once it
# is recorded, and what the Testing recipient's line then says it got.
RESOLUTIONS = [
    (
        "note",
        "Save evidence note",
        "Evidence note saved.",
        "Not sure it arrived",
        "not sure it arrived",
    ),
    (
        "accept",
        "Confirm delivery using external evidence",
        "Delivery confirmed.",
        "Delivered",
        "delivered",
    ),
    (
        "confirm_unsent",
        "Record that the provider did not send it (no resend)",
        "Recorded as not sent.",
        "Failed",
        "not delivered",
    ),
    (
        "resend",
        "Authorize potentially duplicate resend",
        "Resend authorized.",
        None,
        "not delivered yet",
    ),
]


def resolution_form(page, name):
    """The resolution form whose button is named ``name``."""
    return page.locator("#delivery-resolve form").filter(
        has=page.get_by_role("button", name=name, exact=True)
    )


@pytest.mark.parametrize("action, name, message, state, outcome", RESOLUTIONS)
def test_resolutions_are_in_place(
    page, component_origin, action, name, message, state, outcome
):
    """Each resolution posts once (the resend, the riskiest, double-clicked)
    and follows the view's redirect back: the state and recipient lines,
    history, evidence and forms are swapped in where the reader is, so no
    line keeps a stale outcome. Focus goes to the evidence heading, below
    the forms, where the decision is now listed: the fresh form, if still
    offered, waits for a new note."""
    open_marked(page, component_origin, DELIVERY[action])
    posts = count_requests(page, "POST", RESOLVE[action])
    form = resolution_form(page, name)
    form.locator("textarea").fill("Checked the Sent folder")
    if action == "resend":
        form.get_by_role("checkbox").check()
    button = form.get_by_role("button", name=name, exact=True)
    offset = wheel_to(page, button)
    if action == "resend":
        button.dblclick()
    else:
        button.click()
    announced(page, message)
    stayed(page, offset)
    routed = page.locator("#delivery-state #email-routed")
    visible(routed.get_by_text(f"testing@example.org ({outcome})"))
    if outcome != "not sure it arrived":
        assert "not sure it arrived" not in routed.inner_text()
    evidence = page.locator("#delivery-evidence")
    focused(evidence.get_by_role("heading", name="Admin evidence and decisions"))
    visible(evidence.get_by_text(SAVED_NOTE).first)
    if state:
        visible(
            page.locator("#delivery-state > p").first.get_by_text(state, exact=False)
        )
    if action == "note":
        # Still offered, at the new version, emptied and waiting again.
        fresh = resolution_form(page, name)
        has_value(fresh.locator("textarea"), "")
        has_value(fresh.locator("[name=expected_version]"), "5")
        assert fresh.get_by_role("button", name=name, exact=True).is_disabled()
    else:
        # The decision settled the email: only the note is still offered.
        hidden(page.get_by_role("button", name=name, exact=True))
    assert posts == [component_origin + RESOLVE[action]]


def test_note_disclosure_stays_open_after_a_note(page, component_origin):
    """On an email with no decision left, the note form waits in a closed
    disclosure; once a note is saved in place it stays open, so the reader
    can add another without opening it again."""
    open_marked(page, component_origin, DELIVERY["note_only"])
    posts = count_requests(page, "POST", RESOLVE["note_only"])
    page.get_by_text("Add a note to this email's history").click()
    page.locator("#evidence-note").fill("Checked the Sent folder")
    button = page.get_by_role("button", name="Save evidence note", exact=True)
    offset = wheel_to(page, button)
    button.click()
    announced(page, "Evidence note saved.")
    stayed(page, offset)
    assert page.locator("#delivery-note-disclosure").evaluate("node => node.open")
    has_value(page.locator("#evidence-note"), "")
    assert posts == [component_origin + RESOLVE["note_only"]]


def test_stale_resolution_is_shown_once(page, component_origin):
    """A resolution refused because the email changed meanwhile (the view's
    409 for a stale version, whose page draws no region) is shown whole,
    never swallowed, and is not sent again."""
    open_marked(page, component_origin, DELIVERY["stale"])
    posts = count_requests(page, "POST", RESOLVE["stale"])
    form = resolution_form(page, "Save evidence note")
    form.locator("textarea").fill("Checked the Sent folder")
    button = form.get_by_role("button", name="Save evidence note", exact=True)
    wheel_to(page, button)
    button.click()
    visible(page.get_by_role("heading", name="Delivery action unavailable"))
    visible(page.get_by_text("This information changed. Reload before trying again."))
    painted(page)
    assert posts == [component_origin + RESOLVE["stale"]]


def test_paging_keeps_a_note_being_typed(page, component_origin):
    """The resolution forms are a region only their own answers replace
    (data-in-place-own): Next page swaps the history and evidence in place
    and keeps the note the reader is typing."""
    open_marked(page, component_origin, DELIVERY["note"])
    form = resolution_form(page, "Save evidence note")
    form.locator("textarea").fill("Half-written note")
    page.locator("#delivery-history").get_by_role("link", name="Next page").click()
    announced(page, "Next page shown.")
    visible(page.locator("#delivery-evidence").get_by_text("Earlier evidence"))
    has_value(form.locator("textarea"), "Half-written note")
    assert page.evaluate(MARKED) == "kept"
