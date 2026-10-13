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
    """Native validation and keyboard submission preserve closed form identity."""
    page.goto(component_origin + "/delivery")
    assert "acceptance-help" in page.locator("#evidence-accept").get_attribute(
        "aria-describedby"
    )
    assert "mark_unknown" not in page.locator("main").inner_text()
    form = page.locator("form").filter(
        has=page.locator('[name="action"][value="resend"]')
    )
    form.locator("textarea").fill("Confirmed with the Family")
    assert not form.evaluate("form => form.checkValidity()")
    checkbox = form.get_by_role("checkbox")
    checkbox.focus()
    page.keyboard.press("Space")
    assert form.evaluate("form => form.checkValidity()")
    page.route(
        "**/admin/mail/outgoing/*/resolution/",
        lambda route: route.fulfill(status=200, body="Recorded"),
    )
    form.get_by_role("button").focus()
    with page.expect_request("**/admin/mail/outgoing/*/resolution/") as sent:
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
        if value == "bad":
            assert page.locator("[data-background-total]").inner_text() == "0"
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


def test_email_page_names_the_email_and_reads_in_the_requested_order(
    page, component_origin
):
    """#1015: the heading names the email by its type and Family, the
    Family's name sits beside its DUID with the refused address, and the
    page then reads attempt history, Technical details, the latest background
    task, the resolution forms and the Admin evidence, in that order. The
    browser title leaves the Family's name out."""
    page.goto(component_origin + "/delivery")
    heading = page.get_by_role("heading", level=1)
    assert heading.inner_text() == ("Initial invitation to Castellanos, Maximiliana")
    assert "Castellanos" not in page.title()
    main = page.locator("main")
    text = main.inner_text()
    assert "Family: Castellanos, Maximiliana · Family DUID: 12345" in (text)
    refused = main.get_by_role("link", name="head@example.org")
    assert refused.get_attribute("href").startswith("/admin/mail/refusals/")
    order = [
        "Attempt history",
        "Technical details",
        "Latest background task",
        "Resolve this email",
        "Admin evidence and decisions",
    ]
    places = [text.index(label) for label in order]
    assert places == sorted(places)
    resolve = page.locator("section", has=page.locator("#resolve-heading"))
    assert resolve.locator("form").count() == 4
    assert (
        page.locator("label[for=evidence-accept]").inner_text()
        == "Why are you doing this? (saved in this email's history)"
    )


def test_email_without_a_decision_keeps_its_note_form_closed(page, component_origin):
    """#1015: with only the evidence note offered (nothing to decide), there
    is no Resolve section; the note form waits in a closed disclosure."""
    page.goto(component_origin + "/delivery-note-only")
    assert page.locator("#resolve-heading").count() == 0
    disclosure = page.locator("details", has_text="Add a note to this email's history")
    assert disclosure.get_attribute("open") is None
    assert not page.locator("#evidence-note").is_visible()
    # The summary is the form's only heading there (no h3 repeating it).
    assert disclosure.locator("h3").count() == 0
    disclosure.locator("summary").click()
    assert page.locator("#evidence-note").is_visible()
    assert "Not sure it arrived, which" not in page.locator("main").inner_text()


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
    attempt recorded for it, and a line under them says how many addresses
    an accepted attempt reached, before the attempt history."""
    page.goto(component_origin + "/delivery-partial")
    text = page.locator("main").inner_text()
    line = "To: head@example.org (delivered), spouse@example.org (refused for now)"
    # The longer explanation of an address refused for now is in the page's
    # About panel, so only the count shows here.
    reached = "Delivered to 1 of 2 addresses."
    assert line in text and reached in text
    assert text.index(line) < text.index(reached) < text.index("Attempt history")
    assert "Testing mode sends it to" not in text
