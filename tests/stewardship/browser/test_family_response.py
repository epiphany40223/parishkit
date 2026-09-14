"""Real Family JS under CSP, with synthetic boundary responses and no providers."""

from copy import deepcopy
from uuid import uuid4

import pytest

from parishkit.stewardship.responses.census import (
    ADDRESS_LIMITS,
    FAMILY_FIELDS,
    blank_address,
    country_choices,
    us_regions,
)
from parishkit.stewardship.responses.inputs import MEMBER_FIELDS

from ..census_factory import member
from .conftest import NOW

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)


def expect(locator):
    """Load the optional browser assertion library only after explicit opt-in."""
    from playwright.sync_api import expect as browser_expect

    return browser_expect(locator)


def member_field(form, name):
    """Select by stable field name, independent of presentation ordering."""
    return next(
        field for field in form["members"][0]["fields"] if field["name"] == name
    )


def form_payload(*, testing=False):
    """The component fixture uses the same closed schema as the HTTP owner tests."""
    values = member(email="alex@example.org")
    return {
        "baseline": str(uuid4()),
        "testing": testing,
        "modules": ["census"],
        "ministries": None,
        "today": "2026-09-13",
        "family": {"mailingName": "Sample Family", "envelopeNumber": "123"},
        "household": {
            "fields": [
                {
                    "name": field.name,
                    "label": field.label,
                    "kind": field.kind.value,
                    "value": None if field.name == "email_opt_out" else blank_address(),
                    "available": False,
                    "changed": False,
                    "conflict": False,
                }
                for field in FAMILY_FIELDS
            ],
            "mailing_same_as_home": False,
            "address_limits": dict(ADDRESS_LIMITS),
            "countries": country_choices(),
            "us_regions": sorted(us_regions()),
        },
        "members": [
            {
                "id": "3",
                "relationship": "Head",
                "request": None,
                "fields": [
                    {
                        "name": field.name,
                        "label": field.label,
                        "required": field.required,
                        "max_length": field.max_length,
                        "kind": field.kind.value,
                        "choices": list(field.choices),
                        "value": values[field.name],
                        "available": True,
                        "changed": False,
                        "conflict": False,
                    }
                    for field in MEMBER_FIELDS
                ],
            }
        ],
        "proposed_members": [],
        "max_proposed_members": 100,
        "new_member_fields": [
            {
                "name": field.name,
                "label": field.label,
                "required": field.required,
                "max_length": field.max_length,
                "kind": field.kind.value,
                "choices": list(field.choices),
                "value": "",
                "available": False,
                "changed": False,
                "conflict": False,
            }
            for field in MEMBER_FIELDS
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
    member_field(fresh, "first_name")["value"] = "New source first"
    member_field(fresh, "last_name")["value"] = "New source last"

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


@pytest.mark.parametrize("in_flight", [False, True])
def test_accepted_submission_wins_over_local_expiry(page, component_origin, in_flight):
    """R1-03: the authoritative accepted result must survive a local timer race."""
    pending = []

    def submit(route):
        if in_flight:
            pending.append(route)
        else:
            route.fulfill(json={"accepted": True})

    prepare(page, component_origin, submit=submit)
    page.get_by_role("button", name="Begin reviewing").click()
    page.get_by_role("button", name="Review response").click()
    page.get_by_role("button", name="Submit response").click()
    if in_flight:
        expect(page.get_by_role("button", name="Submit response")).to_be_disabled()
        page.clock.fast_forward(4 * 60 * 60 * 1000)
        assert pending
        pending[0].fulfill(json={"accepted": True})
    expect(page.get_by_role("heading", name="Thank you!", exact=True)).to_be_visible()
    page.clock.fast_forward(5 * 60 * 60 * 1000)
    expect(page.get_by_role("heading", name="Thank you!", exact=True)).to_be_visible()
    assert page.locator("#session-warning").is_hidden()
    assert page.locator("#session-expired").is_hidden()
    assert "not been saved" not in page.locator("main").inner_text()


def test_disabled_additional_data_cannot_be_replayed_from_old_response(
    page, component_origin
):
    """R1-04/05: tolerate old hidden text without sending a disabled answer."""
    prepare(page, component_origin)
    form = form_payload()
    form["additional_enabled"] = False
    form["additional_information"] = "Old text that is no longer enabled"
    submissions = []
    page.route("**/family/form", lambda route: route.fulfill(json={"form": form}))

    def submit(route):
        submissions.append(route.request.post_data_json)
        route.fulfill(json={"accepted": True})

    page.route("**/family/submit", submit)
    page.get_by_role("button", name="Begin reviewing").click()
    assert page.locator("#additional-information").count() == 0
    page.get_by_role("button", name="Review response").click()
    page.get_by_role("button", name="Submit response").click()
    expect(page.get_by_role("heading", name="Thank you!", exact=True)).to_be_visible()
    assert submissions[0]["answers"]["additional_information"] == ""


def test_competing_member_and_additional_edits_need_explicit_choices(
    page, component_origin
):
    """R1-10: the tab cannot silently replace newly refreshed competing values."""
    form = form_payload()
    member_field(form, "first_name")["value"] = "Updated record"
    form["additional_information"] = "Another adult's note"
    prepare(
        page,
        component_origin,
        submit=lambda route: route.fulfill(
            status=409,
            json={"error": "review_required", "form": form},
        ),
    )
    page.get_by_role("button", name="Begin reviewing").click()
    page.get_by_label("First name (required)").fill("My proposed name")
    page.get_by_label("Additional information (optional)").fill("My proposed note")
    page.get_by_role("button", name="Review response").click()
    page.get_by_role("button", name="Submit response").click()
    expect(page.locator("[data-conflict]")).to_have_count(2)
    page.get_by_role("button", name="Review response").click()
    assert page.get_by_role("button", name="Submit response").count() == 0
    page.get_by_role("radio", name="Use updated records: Updated record").check()
    page.get_by_role("radio", name="Use my edit: My proposed note").check()
    expect(page.get_by_label("First name (required)")).to_have_value("Updated record")
    expect(page.get_by_label("Additional information (optional)")).to_have_value(
        "My proposed note"
    )
    page.get_by_role("button", name="Review response").click()
    expect(page.get_by_role("button", name="Submit response")).to_be_visible()


def test_conflict_arrows_keep_both_values_until_explicit_review(page, component_origin):
    """Keyboard exploration must not destroy the edit or the alternative value."""
    fresh = form_payload()
    member_field(fresh, "first_name")["value"] = "Updated records"
    prepare(
        page,
        component_origin,
        submit=lambda route: route.fulfill(
            status=409, json={"error": "review_required", "form": fresh}
        ),
    )
    page.get_by_role("button", name="Begin reviewing").click()
    page.get_by_label("First name (required)").fill("My edit")
    page.get_by_role("button", name="Review response").click()
    page.get_by_role("button", name="Submit response").click()
    mine = page.get_by_role("radio", name="Use my edit: My edit")
    records = page.get_by_role("radio", name="Use updated records: Updated records")
    mine.focus()
    page.keyboard.press("ArrowDown")
    expect(records).to_be_checked()
    expect(mine).to_be_visible()
    page.keyboard.press("ArrowUp")
    expect(mine).to_be_checked()
    expect(records).to_be_visible()
    expect(page.get_by_label("First name (required)")).to_have_value("My edit")
    page.get_by_role("button", name="Review response").click()
    expect(page.get_by_role("button", name="Submit response")).to_be_visible()
    assert "My edit" in page.locator("main").inner_text()


@pytest.mark.parametrize("error", ["validation", "review_required"])
def test_expiry_after_definite_rejection_warns_changes_were_not_saved(
    page, component_origin, error
):
    """Only an indeterminate/in-flight request uses the ambiguous status warning."""
    payload = {
        "error": error,
        "form": form_payload(),
        "fields": {"members.3.first_name": "Enter a valid name."},
    }
    prepare(
        page,
        component_origin,
        submit=lambda route: route.fulfill(status=409, json=payload),
    )
    page.get_by_role("button", name="Begin reviewing").click()
    page.get_by_role("button", name="Review response").click()
    page.get_by_role("button", name="Submit response").click()
    expect(page.get_by_role("button", name="Review response")).to_be_visible()
    page.clock.fast_forward(3_700_000)
    expect(page.locator("#family-flow-message")).to_contain_text(
        "Unsubmitted changes have not been saved"
    )
    assert "check your last submission time" not in page.locator("main").inner_text()


@pytest.mark.parametrize("retry", ["forbidden", "validation"])
def test_uncertain_submit_survives_a_definitely_rejected_retry(
    page, component_origin, retry
):
    """A rejected retry cannot prove that the earlier lost response did not commit."""
    calls = []

    def respond(route):
        """Model a lost first reply followed by one definitely rejected request."""
        calls.append(True)
        if len(calls) == 1:
            route.abort()
        elif retry == "forbidden":
            route.fulfill(status=403, body="Forbidden")
        else:
            route.fulfill(
                status=400,
                json={
                    "error": "validation",
                    "fields": {"members.3.first_name": "Review the name."},
                },
            )

    prepare(page, component_origin, submit=respond)
    page.get_by_role("button", name="Begin reviewing").click()
    page.get_by_role("button", name="Review response").click()
    page.get_by_role("button", name="Submit response").click()
    expect(page.locator("#family-flow-message")).to_contain_text("could not confirm")
    page.get_by_role("button", name="Submit response").click()
    if retry == "validation":
        expect(page.get_by_role("button", name="Review response")).to_be_visible()
        page.clock.fast_forward(3_700_000)
    expect(page.locator("#family-flow-message")).to_contain_text(
        "check your last submission time"
    )
    assert (
        "Unsubmitted changes have not been saved"
        not in page.locator("main").inner_text()
    )
