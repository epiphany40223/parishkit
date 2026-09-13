"""Real Family JS under CSP, with synthetic boundary responses and no providers."""

from copy import deepcopy
from uuid import uuid4

import pytest
from playwright.sync_api import expect

from parishkit.stewardship.responses.inputs import MEMBER_FIELDS

from .conftest import NOW

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)


def form_payload(*, testing=False):
    """The component fixture uses the same closed schema as the HTTP owner tests."""
    values = {
        "first_name": "Alex",
        "middle_name": "",
        "last_name": "Sample",
        "email": "alex@example.org",
    }
    return {
        "baseline": str(uuid4()),
        "testing": testing,
        "family": {"mailingName": "Sample Family", "envelopeNumber": "123"},
        "members": [
            {
                "id": "3",
                "fields": [
                    {
                        "name": field.name,
                        "label": field.label,
                        "required": field.required,
                        "max_length": field.max_length,
                        "value": values[field.name],
                        "available": True,
                        "changed": False,
                        "conflict": False,
                    }
                    for field in MEMBER_FIELDS
                ],
            }
        ],
        "additional_enabled": True,
        "additional_max_length": 5000,
        "additional_information": "",
        "last_submitted_at": None,
        "content": {"thank_you": "<p>Thank you for helping our parish.</p>"},
    }


def prepare(page, origin, *, testing=False, submit=None):
    """Capture boundary traffic; presence is explicitly answer-free and separate."""
    page.clock.install(time=NOW)
    attempts = []

    def begin(route):
        """Return fixture data only after the actual browser consent action."""
        attempts.append(route.request)
        route.fulfill(json={"form": form_payload(testing=testing)})

    page.route("**/family/form", begin)
    page.route(
        "**/family/presence", lambda route: route.fulfill(json={"recorded": True})
    )
    if submit:
        page.route("**/family/submit", submit)
    page.goto(origin + ("/family-testing" if testing else "/family"))
    return attempts


@pytest.mark.parametrize("width", [320, 1280])
def test_no_change_flow_accessibility_mobile_and_no_draft_traffic(
    page, component_origin, axe_source, width
):
    """Next/back do not transmit answers; only definitive Submit sends the aggregate."""
    page.set_viewport_size({"width": width, "height": 900})
    submissions, failures = [], []
    page.on("pageerror", lambda error: failures.append(str(error)))

    def submit(route):
        """The confirmation boundary is the only place answers leave this tab."""
        submissions.append(route.request.post_data_json)
        route.fulfill(json={"accepted": True})

    attempts = prepare(page, component_origin, submit=submit)
    assert attempts == []
    page.get_by_role("button", name="Begin reviewing").click()
    expect(page.get_by_label("First name (required)")).to_have_value("Alex")
    assert attempts[0].post_data_json == {"testing_acknowledged": False}
    assert attempts[0].headers["x-csrftoken"] == "a" * 64
    assert page.locator(":focus").inner_text() == "Step 1 of 2: Review your household"
    page.evaluate(axe_source)
    assert (
        page.evaluate("""async () => (await axe.run(document, {
        runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa']}
    })).violations.map(v => v.id)""")
        == []
    )
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.get_by_role("button", name="Review response").click()
    page.get_by_role("button", name="Back to edit").click()
    assert len(attempts) == 1 and not submissions
    assert page.evaluate("localStorage.length + sessionStorage.length") == 0
    page.get_by_role("button", name="Review response").click()
    page.get_by_role("button", name="Submit response").click()
    expect(page.get_by_role("heading", name="Thank you!", exact=True)).to_be_visible()
    assert len(submissions) == 1
    assert submissions[0]["answers"]["members"]["3"]["first_name"] == "Alex"
    assert "alex@example.org" not in page.locator("body").inner_text()
    assert page.locator("#family-cancel").is_hidden()
    assert page.evaluate("localStorage.length + sessionStorage.length") == 0
    assert not failures


def test_testing_requires_two_independent_unchecked_acknowledgments(
    page, component_origin
):
    """Entry consent cannot become final consent or expose a live submission count."""
    submissions = []

    def submit(route):
        submissions.append(route.request.post_data_json)
        route.fulfill(json={"accepted": True})

    attempts = prepare(page, component_origin, testing=True, submit=submit)
    page.get_by_role("button", name="Begin reviewing").click()
    assert not attempts and page.get_by_label("First name (required)").count() == 0
    page.locator("#testing-entry-ack").check()
    page.get_by_role("button", name="Begin reviewing").click()
    page.get_by_role("button", name="Review response").click()
    assert not page.locator("#testing-submit-ack").is_checked()
    page.get_by_role("button", name="Submit response").click()
    assert not submissions
    page.locator("#testing-submit-ack").check()
    page.get_by_role("button", name="Back to edit").click()
    page.get_by_role("button", name="Review response").click()
    assert not page.locator("#testing-submit-ack").is_checked()
    page.locator("#testing-submit-ack").check()
    page.get_by_role("button", name="Submit response").click()
    expect(page.get_by_role("heading", name="Thank you!", exact=True)).to_be_visible()
    assert submissions[0]["answers"]["testing_acknowledged"] is True
    assert "will not count" in page.locator("main").inner_text()


def test_stale_response_keeps_only_actual_edits_and_requires_review(
    page, component_origin
):
    """A refreshed source name is adopted only when that field was not edited."""
    submissions = []
    fresh = form_payload()
    fresh["members"][0]["fields"][0]["value"] = "New source first"
    fresh["members"][0]["fields"][2]["value"] = "New source last"

    def submit(route):
        submissions.append(deepcopy(route.request.post_data_json))
        route.fulfill(status=409, json={"error": "review_required", "form": fresh})

    prepare(page, component_origin, submit=submit)
    page.get_by_role("button", name="Begin reviewing").click()
    page.get_by_label("First name (required)").fill("My edit")
    page.get_by_role("button", name="Review response").click()
    page.get_by_role("button", name="Submit response").click()
    expect(page.get_by_label("First name (required)")).to_have_value("My edit")
    expect(page.get_by_label("Last name (required)")).to_have_value("New source last")
    assert len(submissions) == 1
    assert (
        "Please review everything" in page.locator("#family-flow-message").inner_text()
    )


def test_expiry_erases_sensitive_form_and_never_submits(page, component_origin):
    """In-memory state and rendered fields are both discarded on session expiry."""
    submissions = []
    prepare(
        page, component_origin, submit=lambda route: submissions.append(route.request)
    )
    page.get_by_role("button", name="Begin reviewing").click()
    page.get_by_label("First name (required)").fill("Private tab edit")
    page.clock.fast_forward(4 * 60 * 60 * 1000)
    expect(page.locator("#family-cancel")).to_be_hidden()
    assert page.locator("#member-3-first_name").count() == 0
    assert "Private tab edit" not in page.content()
    assert not submissions
    assert page.evaluate("localStorage.length + sessionStorage.length") == 0


def test_invalid_email_blur_and_answer_markup_stays_text(page, component_origin):
    """Browser validation blocks progression and answer values never become HTML."""
    prepare(page, component_origin)
    page.get_by_role("button", name="Begin reviewing").click()
    email = page.get_by_label("Email address (optional)")
    email.fill("invalid")
    page.get_by_label("First name (required)").fill("<img src=x onerror=alert(1)>")
    expect(email).to_have_attribute("aria-invalid", "true")
    page.get_by_role("button", name="Review response").click()
    assert page.get_by_role("button", name="Submit response").count() == 0
    email.fill("valid@example.org")
    page.get_by_role("button", name="Review response").click()
    expect(page.get_by_role("button", name="Submit response")).to_be_visible()
    assert page.locator("#family-flow img").count() == 0
    assert "<img src=x onerror=alert(1)>" in page.locator("#family-flow").inner_text()
