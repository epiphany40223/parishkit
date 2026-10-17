"""System logs filters refused at their field, not on an error page (#1332).

The server refuses nothing ticked, a part identifier, a malformed date and
dates out of order by drawing the page again, answered 400, with the
values sent, the error at the field and in the summary, and the entries
for no filters. The fixtures (log_components) are served at the address
the page's forms post to (LIVE), so an in-place answer is this same page.
"""

import pytest

from .log_components import LIVE
from .test_charts import assert_clean, watch

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

ORDER = "Enter a whole Through date, no earlier than From, or clear it."
NONE = "Tick at least one kind of entry to show."
# Nothing ticked under an audit-only Activity: Audit record's own hint.
AUDIT_ONLY = "This activity is made of audit records only; tick Audit record."
IDENTIFIER = (
    "Paste a whole identifier from the Technical details of an entry, or clear it."
)
BOXES = ("debug", "info", "warning", "error", "critical", "audit")
# Apply's top in page coordinates.
APPLY_BOX = (
    "document.querySelector('#table-filters [type=submit]')"
    ".getBoundingClientRect().y + scrollY"
)
# How far below Apply the content after the filters starts (Filter by
# identifier): a refusal's message clearing must not change it.
FOLLOWING = """() => {
    const top = (selector) => document.querySelector(selector)
        .getBoundingClientRect().top;
    return top("#log-identifier-filters") - top("#table-filters [type=submit]");
}"""


def assert_in_hint_row(page, message):
    """The server's ``message`` shows in the gate's reserved hint row.

    It is the one message line, laid over the gate's hint (hidden while it
    shows), at the hint's own place, so no line is reserved for it.
    """
    from playwright.sync_api import expect

    line = page.locator("#log-filters_error")
    expect(line).to_have_text(message)
    expect(line).to_be_visible()
    assert page.locator(".field-error-line").count() == 1
    row = page.locator(".log-filter-messages")
    expect(row.locator("#log-filter-hint")).to_have_count(1)
    expect(row.locator("#log-filters_error")).to_have_count(1)
    hint = page.locator("#log-filter-hint")
    assert hint.evaluate("node => getComputedStyle(node).visibility") == "hidden"
    assert abs(line.bounding_box()["y"] - hint.bounding_box()["y"]) < 1


def described(page, selector):
    """The ids that describe ``selector``, in order."""
    return (page.locator(selector).get_attribute("aria-describedby") or "").split()


def assert_refused_at_the_dates(page):
    """Both dates are marked and share one message; the summary links Through.

    Each date keeps its zone note before the message, in one
    aria-describedby.
    """
    from playwright.sync_api import expect

    for end in ("#log-start", "#log-end"):
        expect(page.locator(end)).to_have_attribute("aria-invalid", "true")
        assert described(page, end) == ["log-zone-help", "log-filters_error"]
    assert_in_hint_row(page, ORDER)
    summary = page.locator("[data-error-summary]")
    expect(summary).to_have_count(1)
    link = summary.get_by_role("link", name=ORDER)
    expect(link).to_have_attribute("href", "#log-end")


def assert_cleared(page, fields, line, kept=()):
    """No mark, message or summary item is left on ``fields``.

    ``kept`` is each field's own description, which stays.
    """
    from playwright.sync_api import expect

    for field, own in zip(fields, kept or [None] * len(fields), strict=True):
        expect(page.locator(field)).not_to_have_attribute("aria-invalid", "true")
        assert described(page, field) == ([own] if own else [])
    expect(page.locator(f"#{line}_error")).to_be_hidden()
    expect(page.locator("[data-error-summary]")).to_have_count(0)
    # The gate's hint shows again in its row.
    hint = page.locator("#log-filter-hint")
    assert hint.evaluate("node => getComputedStyle(node).visibility") == "visible"


@pytest.mark.parametrize("edited", ["#log-end", "#log-start"])
def test_refused_dates_are_shown_at_their_fields(page, component_origin, edited):
    """Dates the server refused out of order are drawn again marked.

    The dates keep the values sent, the summary takes focus and its link
    moves focus to Through, and an edit of either date clears both marks,
    the message and the summary, keeping the zone note.
    """
    from playwright.sync_api import expect

    errors = watch(page)
    page.goto(component_origin + "/logs-refused-dates")
    assert_refused_at_the_dates(page)
    expect(page.locator("[data-error-summary]")).to_be_focused()
    expect(page.locator("#log-start")).to_have_value("2026-09-20")
    expect(page.locator("#log-end")).to_have_value("2026-09-19")
    page.locator("[data-error-summary] a").click()
    expect(page.locator("#log-end")).to_be_focused()
    following = page.evaluate(FOLLOWING)
    page.locator(edited).fill("2026-09-01" if edited == "#log-start" else "2026-09-30")
    # The row keeps the message's height once it clears: nothing moves.
    assert abs(page.evaluate(FOLLOWING) - following) < 1
    assert_cleared(
        page, ("#log-start", "#log-end"), "log-filters", ("log-zone-help",) * 2
    )
    assert_clean(page, errors)


@pytest.mark.parametrize(
    ("fixture", "target", "message"),
    [
        ("/logs-refused-none", "#log-show-debug", NONE),
        # An audit-only Activity greys the levels: Audit record is linked,
        # and the refusal says what Audit record's hint says.
        ("/logs-refused-none-sign-in", "#log-show-audit", AUDIT_ONLY),
    ],
)
def test_refused_nothing_ticked_is_shown_at_the_show_choices(
    page, component_origin, fixture, target, message
):
    """Nothing ticked is drawn again with the Show boxes marked (#1332).

    The summary links to a box that can take focus, the search sent is
    kept, and ticking a box clears every mark, the message and the summary.
    """
    from playwright.sync_api import expect

    errors = watch(page)
    page.goto(component_origin + fixture)
    # Under an audit-only Activity only Audit record is marked: the greyed
    # level boxes take no mark, which would clear with them.
    marked = BOXES if target == "#log-show-debug" else ("audit",)
    for box in BOXES:
        field = page.locator(f"#log-show-{box}")
        expect(field).not_to_be_checked()
        if box in marked:
            expect(field).to_have_attribute("aria-invalid", "true")
            assert described(page, f"#log-show-{box}") == ["log-filters_error"]
        else:
            expect(field).to_be_disabled()
            expect(field).not_to_have_attribute("aria-invalid", "true")
    assert_in_hint_row(page, message)
    # The gate's own hint, hidden under it, says the same words.
    expect(page.locator("#log-filter-hint")).to_have_text(message)
    summary = page.locator("[data-error-summary]")
    expect(summary.get_by_role("link", name=message)).to_have_attribute("href", target)
    if fixture == "/logs-refused-none":
        expect(page.locator("#log-text")).to_have_value("lag")
    summary.get_by_role("link").click()
    expect(page.locator(target)).to_be_focused()
    page.locator("#log-show-audit").check()
    assert_cleared(page, [f"#log-show-{box}" for box in marked], "log-filters")
    expect(page.get_by_role("button", name="Apply filters")).to_be_enabled()
    assert_clean(page, errors)


def test_refused_identifier_opens_its_filters(page, component_origin):
    """A refused identifier opens Filter by identifier with its field marked.

    The field keeps its own help before the identifiers' message, and an
    edit clears the mark, the message and the summary.
    """
    from playwright.sync_api import expect

    errors = watch(page)
    page.goto(component_origin + "/logs-refused-subject")
    assert page.locator("#log-identifier-filters").evaluate("node => node.open")
    subject = page.locator("#log-subject")
    expect(subject).to_have_attribute("aria-invalid", "true")
    assert described(page, "#log-subject") == ["log-subject-help", "log-filters_error"]
    assert_in_hint_row(page, IDENTIFIER)
    # The other identifiers are neither marked nor described by it.
    expect(page.locator("#log-actor")).not_to_have_attribute("aria-invalid", "true")
    page.locator("[data-error-summary] a").click()
    expect(subject).to_be_focused()
    following = page.evaluate(FOLLOWING)
    subject.fill("")
    assert abs(page.evaluate(FOLLOWING) - following) < 1
    assert_cleared(page, ("#log-subject",), "log-filters", ("log-subject-help",))
    assert_clean(page, errors)


def test_fixing_the_other_date_and_applying_clears_the_mark(page, component_origin):
    """A successful in-place Apply clears the server marks it does not repeat.

    The reader fixes the order at From, the other end (whose edit clears
    the shared mark at once), and Apply's answer marks nothing: no mark,
    message or summary is left (#1332).
    """

    errors = watch(page)
    answer = page.request.get(component_origin + LIVE).text()
    page.goto(component_origin + "/logs-refused-dates")
    page.route(
        "**" + LIVE,
        lambda route: route.fulfill(body=answer, content_type="text/html"),
    )
    page.locator("#log-start").fill("2026-09-01")
    # A mark a later answer must clear, set as an earlier answer would have
    # left it had the edit not cleared it (on the date not touched).
    page.evaluate("""() => {
        const field = document.getElementById("log-end");
        field.setAttribute("aria-invalid", "true");
        field.setAttribute("data-field-error", "");
        field.setAttribute("aria-describedby", "log-zone-help log-filters_error");
        const line = document.getElementById("log-filters_error");
        line.hidden = false;
        line.querySelector("li").textContent = "stale";
    }""")
    with page.expect_response("**" + LIVE):
        page.get_by_role("button", name="Apply filters").click()
    assert_cleared(
        page, ("#log-start", "#log-end"), "log-filters", ("log-zone-help",) * 2
    )
    assert_clean(page, errors)


def test_in_place_apply_refused_marks_the_kept_dates(page, component_origin):
    """A refused in-place Apply marks the dates on the form it kept (#1332).

    No reload and no shift: the form keeps what was typed and takes the
    marks and message from the 400 answer into its reserved line, so Apply
    stays under the pointer, and focus goes straight from Apply to the
    summary in the results, never through a date.
    """
    from playwright.sync_api import expect

    errors = watch(page)
    answer = page.request.get(component_origin + "/logs-refused-dates").text()
    page.goto(component_origin + LIVE)
    page.evaluate("window.__logsMarker = 1")
    page.route(
        "**" + LIVE,
        lambda route: route.fulfill(status=400, body=answer, content_type="text/html"),
    )
    # The dates typed are in order: the complete gate holds Apply while they
    # are out of order, so the out-of-order refusal can only be the answer's
    # here, standing in for a hand-built request that reached the server.
    page.locator("#log-start").fill("2026-09-18")
    page.locator("#log-end").fill("2026-09-19")
    before = page.evaluate(APPLY_BOX)
    page.evaluate("""() => {
        window.__focused = [];
        document.addEventListener("focusin", (event) => {
            window.__focused.push(event.target.id || event.target.tagName);
        });
    }""")
    page.get_by_role("button", name="Apply filters").click()
    assert_refused_at_the_dates(page)
    summary = page.locator("[data-table-region] [data-error-summary]")
    expect(summary).to_be_focused()
    focused = page.evaluate("window.__focused")
    assert "log-start" not in focused and "log-end" not in focused
    assert abs(page.evaluate(APPLY_BOX) - before) < 1
    assert page.evaluate("window.__logsMarker") == 1
    # What was typed stays; the answer's own values are not written over it.
    expect(page.locator("#log-start")).to_have_value("2026-09-18")
    expect(page.locator("#log-end")).to_have_value("2026-09-19")
    page.locator("#log-end").fill("2026-09-20")
    assert_cleared(
        page, ("#log-start", "#log-end"), "log-filters", ("log-zone-help",) * 2
    )
    # The browser reports the answer's 400 itself; nothing else may appear.
    assert_clean(page, [error for error in errors if "status of 400" not in error])


def test_refused_hostile_identifier_stays_text(page, component_origin):
    """A hostile value sent back into a refused identifier is never markup."""
    from playwright.sync_api import expect

    errors = watch(page)
    page.goto(component_origin + "/logs-refused-hostile")
    expect(page.locator("#log-actor")).to_have_attribute("aria-invalid", "true")
    assert page.locator("#log-actor").get_attribute("value") == (
        '"><script>window.__pwned = 1</script>'
    )
    assert page.evaluate("window.__pwned") is None
    assert page.locator("#table-filters script").count() == 0
    assert_clean(page, errors)


@pytest.mark.parametrize("width", [320, 1280])
def test_unrefused_page_reserves_no_message_line(page, component_origin, width):
    """An unrefused page is no taller for the refusal message (#1332).

    The message's line sits in the gate's hint row and takes no height
    there, so the row is the hint's one reserved line, with the identifier
    filters closed and open.
    """
    from playwright.sync_api import expect

    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + LIVE)
    row = page.locator(".log-filter-messages")
    hint = page.locator("#log-filter-hint")
    expect(page.locator("#log-filters_error")).to_be_hidden()
    assert page.locator(".field-error-size").text_content() == ""
    # The row is the empty hint's one reserved line (its min-height, 1lh).
    reserved = hint.evaluate("node => parseFloat(getComputedStyle(node).minHeight)")
    assert abs(row.bounding_box()["height"] - reserved) < 0.5
    assert abs(hint.bounding_box()["height"] - reserved) < 0.5
    # Filter by identifier starts right after the hint row's own margin.
    gap = page.evaluate("""() => {
        const row = document.querySelector(".log-filter-messages");
        const next = document.getElementById("log-identifier-filters");
        return next.getBoundingClientRect().top - row.getBoundingClientRect().bottom;
    }""")
    # Sub-pixel tolerance: Firefox lays out 16.000015, not exactly 16.
    margin = page.evaluate(
        "parseFloat(getComputedStyle(document.querySelector('.log-filter-messages'))"
        ".marginBottom)"
    )
    assert abs(gap - margin) < 0.5
    page.locator("#log-identifier-filters summary").click()
    # The identifiers' bar holds no message line of its own.
    assert page.locator("#log-identifier-filters .field-error-line").count() == 0
