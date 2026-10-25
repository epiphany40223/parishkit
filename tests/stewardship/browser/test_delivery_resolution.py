"""Evidence forms and durable warning behavior in all supported browser engines."""

from urllib.parse import parse_qs

import pytest

from .conftest import NOW

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)


def test_keyboard_resend_requires_evidence_and_explicit_duplicate_ack(
    page, component_origin
):
    """The question is answered from the keyboard, native validation holds
    the resend until its note and tick, and the answer posts only its own
    form's closed fields (#1563)."""
    page.goto(component_origin + "/delivery")
    assert "mark_unknown" not in page.locator("main").inner_text()
    for name in ("It was not sent", "Yes"):
        page.get_by_role("radio", name=name, exact=True).focus()
        page.keyboard.press("Space")
    form = page.locator("#answer-resend")
    form.locator("textarea").fill("Confirmed with the Family")
    assert not form.evaluate("form => form.checkValidity()")
    checkbox = form.get_by_role("checkbox")
    checkbox.focus()
    page.keyboard.press("Space")
    assert form.evaluate("form => form.checkValidity()")
    page.route(
        "**/admin/mail/sent/*/resolution/",
        lambda route: route.fulfill(status=200, body="Recorded"),
    )
    form.get_by_role("button").focus()
    with page.expect_request("**/admin/mail/sent/*/resolution/") as sent:
        page.keyboard.press("Enter")
    assert sent.value.method == "POST"
    fields = parse_qs(sent.value.post_data)
    assert set(fields) == {
        "csrfmiddlewaretoken",
        "command_id",
        "expected_version",
        "action",
        "note",
        "duplicate_acknowledged",
    }
    assert fields["action"] == ["resend"]
    assert fields["duplicate_acknowledged"] == ["yes"]
    assert "Confirmed" not in sent.value.url


def test_warning_polls_durable_count_and_survives_unavailable_response(
    page, component_origin
):
    """Failed polling never hides uncertainty or turns a GET into session activity."""
    from playwright.sync_api import expect

    page.clock.install(time=NOW)
    count = [1234]
    requests = []

    def result(route):
        """Use only operational counts; never provide delivery details in polling."""
        requests.append(route.request)
        if count[0] is None:
            route.fulfill(status=503, body="Unavailable")
        else:
            route.fulfill(
                json={
                    "counts": dict(
                        queued=99 if count[0] == "bad" else 0,
                        running=0,
                        retry_wait=0,
                        abandoned=0,
                        active=0,
                    ),
                    "delivery_unknown": count[0],
                    # The other mode's count, its own link (#1576).
                    "delivery_unknown_other": 5 if count[0] == 1234 else 0,
                }
            )

    page.route("**/admin/background/counts", result)
    page.goto(component_origin + "/delivery")
    warning = page.locator("[data-delivery-warning]")
    assert warning.locator("..").get_attribute("aria-live") == "polite"
    unavailable = page.locator("[data-background-unavailable]")
    for index, value in enumerate((1234, 1234, None, "bad", 0)):
        count[0] = value
        failed = value is None or value == "bad"
        # This separate indicator is the poll handler's final success/error
        # action. Give it the opposite state so even unchanged warning values
        # have an observable completion edge. Do not touch the warning itself.
        unavailable.evaluate(
            "(element, hidden) => { element.hidden = hidden; }", failed
        )
        with page.expect_response("**/admin/background/counts"):
            page.clock.fast_forward(30000)
        if failed:
            expect(unavailable).to_be_visible()
        else:
            expect(unavailable).to_be_hidden()
        if value == 0:
            # Response arrival precedes asynchronous JSON handling and DOM writes.
            # Wait for application state, not an arbitrary sleep or a retry run.
            expect(warning).to_be_hidden()
        else:
            expect(warning).to_be_visible()
            expect(warning).to_contain_text("1,234")
            other = warning.locator("[data-delivery-other]")
            expect(other).to_be_visible()
            expect(other).to_have_text("Not sure it arrived in Testing: 5")
            expect(other).to_have_attribute(
                "href", "/admin/mail/sent/?state=needs_review&mode=testing"
            )
        if value == "bad":
            # The failure sentence covers the counts (#1643); the counts
            # themselves still hold the last good total.
            assert page.locator("[data-background-total]").text_content() == "0"
        if index == 0:
            warning.evaluate("""element => {
                window.warningMutations = 0;
                new MutationObserver(records => {
                    window.warningMutations += records.length;
                })
                  .observe(element, {childList: true, subtree: true, attributes: true});
            }""")
        elif index == 1:
            assert page.evaluate("window.warningMutations") == 0
    assert len(requests) == 5 and all(request.method == "GET" for request in requests)


def test_other_mode_link_stays_put_when_this_mode_clears(page, component_origin):
    """#1576 review: when the poll hides the operating mode's link, the other
    mode's link, which comes first, keeps its place under the pointer."""
    from playwright.sync_api import expect

    page.clock.install(time=NOW)
    here = [3]

    def result(route):
        """Counts with the other mode's 5 and this mode's current count."""
        route.fulfill(
            json={
                "counts": dict.fromkeys(
                    ("queued", "running", "retry_wait", "abandoned", "active"), 0
                ),
                "delivery_unknown": here[0],
                "delivery_unknown_other": 5,
            }
        )

    page.route("**/admin/background/counts", result)
    page.goto(component_origin + "/delivery")
    warning = page.locator("[data-delivery-warning]")
    other = warning.locator("[data-delivery-other]")
    mine = warning.locator("[data-delivery-here]")
    with page.expect_response("**/admin/background/counts"):
        page.clock.fast_forward(30000)
    expect(mine).to_have_text("Not sure it arrived — review needed: 3")
    expect(other).to_be_visible()
    before = other.bounding_box()
    other.hover()
    here[0] = 0
    with page.expect_response("**/admin/background/counts"):
        page.clock.fast_forward(30000)
    expect(mine).to_be_hidden()
    expect(other).to_be_visible()
    assert other.bounding_box() == before


def test_email_page_names_the_email_and_reads_in_the_requested_order(
    page, component_origin
):
    """#1015, #1563: the heading names the email by its type and Family, the
    outcome panel and its question come first, then the Family's name
    beside its DUID with the refused address, attempt history, Technical
    details, the latest background task and the Admin evidence, in that
    order. The browser title leaves the Family's name out."""
    page.goto(component_origin + "/delivery")
    heading = page.get_by_role("heading", level=1)
    assert heading.inner_text() == ("Initial invitation to Castellanos, Maximiliana")
    assert "Castellanos" not in page.title()
    main = page.locator("main")
    text = main.inner_text()
    assert "Family: Castellanos, Maximiliana · Family DUID: 12345" in (text)
    refused = main.get_by_role("link", name="head@example.org")
    assert refused.get_attribute("href").startswith("/admin/mail/refusals/")
    # The outcome panel comes first (#1563), then the details as before.
    order = [
        "Not sure it arrived",
        "What did you find?",
        "Family: Castellanos",
        "Attempt history",
        "Latest background task",
        "Admin evidence and decisions",
    ]
    places = [text.index(label) for label in order]
    assert places == sorted(places)
    assert page.locator("#delivery-resolve form").count() == 4
    page.get_by_role("radio", name="It was sent", exact=True).check()
    assert (
        page.locator("label[for=note-sent]").inner_text()
        == "Where did you see that it was sent?"
    )


def test_email_without_a_decision_keeps_its_note_form_closed(page, component_origin):
    """#1015, #1563: a delivered email's panel says so and asks nothing; the
    note form waits in a closed disclosure, and so does the history."""
    page.goto(component_origin + "/delivery-note-only")
    heading = page.locator("#outcome-heading")
    assert heading.inner_text() == "Delivered"
    assert page.locator("#delivery-resolve form").count() == 0
    assert page.locator("main").get_by_role("radio").count() == 0
    disclosure = page.locator("details", has_text="Add a note to this email's history")
    assert disclosure.get_attribute("open") is None
    assert not page.locator("#evidence-note").is_visible()
    # The summary is the form's only heading there (no h3 repeating it).
    assert disclosure.locator("h3").count() == 0
    disclosure.locator("summary").click()
    assert page.locator("#evidence-note").is_visible()
    history = page.locator("#attempt-history-disclosure")
    assert history.get_attribute("open") is None


def test_email_page_shows_its_recipients_without_moving(page, component_origin):
    """#1024: the recipient addresses sit under the Family line, with the
    Testing recipient named when the email was routed there; they are in
    the page as served and stay put while the history pages in place."""
    page.goto(component_origin + "/delivery-paged")
    main = page.locator("main")
    text = main.inner_text()
    line = "To: head@example.org, spouse@example.org"
    assert line in text
    assert "Testing mode sends it to: testing@example.org (not sure it arrived)" in text
    assert (
        text.index("Family DUID:") < text.index(line) < text.index("Refused addresses:")
    )
    # The line's place in the document (not the viewport, which paging may
    # scroll) and size are unchanged after the history pages in place.
    recipients = page.locator("#email-recipients")
    place = "e => { const r = e.getBoundingClientRect();"
    place += " return [r.left, r.top + window.scrollY, r.width, r.height]; }"
    before = recipients.evaluate(place)
    page.locator("#delivery-history").get_by_role("link", name="Next page").click()
    page.locator("#delivery-history").get_by_text("Created").wait_for()
    assert "/delivery-paged?page=2" in page.url
    # Within a pixel: Firefox reports sub-pixel scroll offsets that differ
    # by float rounding after the swap.
    assert recipients.evaluate(place) == pytest.approx(before, abs=1)


def test_email_page_shows_what_each_address_got(page, component_origin):
    """#806: in Production each To: address is followed by what the latest
    attempt recorded for it, and the outcome panel above them says how many
    addresses an accepted attempt reached (#1563)."""
    page.goto(component_origin + "/delivery-partial")
    text = page.locator("main").inner_text()
    line = "To: head@example.org (delivered), spouse@example.org (refused for now)"
    # The longer explanation of an address refused for now is in the page's
    # About panel, so only the count shows here.
    reached = "Delivered to 1 of 2 addresses."
    assert line in text and reached in text
    # The count is the outcome panel's summary, above the addresses (#1563).
    assert text.index(reached) < text.index(line) < text.index("Attempt history")
    assert "Testing mode sends it to" not in text


def test_email_page_links_a_users_address(page, component_origin):
    """#1885: an intended address that is a current user's links that
    user's Timeline by the user's record id, the address staying the text;
    another address, and the Testing recipient, stay plain text."""
    from .delivery_components import SPOUSE_TIMELINE

    page.goto(component_origin + "/delivery")
    recipients = page.locator("#email-recipients")
    assert recipients.inner_text() == "To: head@example.org, spouse@example.org"
    links = recipients.get_by_role("link")
    assert links.count() == 1
    assert links.first.inner_text() == "spouse@example.org"
    assert links.first.get_attribute("href") == SPOUSE_TIMELINE
    assert "@" not in SPOUSE_TIMELINE
    assert page.locator("#email-routed").get_by_role("link").count() == 0
