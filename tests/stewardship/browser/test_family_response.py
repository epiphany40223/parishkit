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


def show(page, locator):
    """Open the Family form page that holds ``locator`` and return it.

    The form shows one page at a time; other pages stay in the DOM but hidden.
    This uses the page's own step-bar segment, so it follows the same code path
    as a Family jumping to a page. It dispatches the click directly: these tests
    exercise their own behavior, and real navigation is covered in
    test_family_pages.py.
    """
    key = locator.first.evaluate("e => e.closest('[data-page]')?.dataset.page || ''")
    if key and not locator.first.is_visible():
        page.locator(f'[data-step-link="{key}"]').dispatch_event("click")
    return locator


def visit_every_page(page):
    """Open each form page once, as a Family must before Review."""
    links = page.locator("[data-step-link]")
    # The step bar is built once the form loads; count only after it exists.
    expect(links.first).to_be_attached()
    for index in range(links.count()):
        links.nth(index).dispatch_event("click")


def review(page):
    """Visit every form page, then select Review response on the last one."""
    visit_every_page(page)
    page.get_by_role("button", name="Review response").click()


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
        "parish_name": "Sample Parish",
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
        "last_submitted_display": None,
        "content": {"thank_you": "<p>Thank you for helping our parish.</p>"},
    }


def prepare(page, origin, *, testing=False, submit=None, form=None):
    """Capture boundary traffic; presence is explicitly answer-free and separate."""
    page.clock.install(time=NOW)
    attempts = []

    def begin(route):
        """Return fixture data only after the actual browser consent action."""
        attempts.append(route.request)
        route.fulfill(json={"form": form or form_payload(testing=testing)})

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
    assert attempts[0].post_data_json == {}
    assert attempts[0].headers["x-csrftoken"] == "a" * 64
    assert page.locator(":focus").inner_text() == "Welcome"
    page.evaluate(axe_source)
    assert (
        page.evaluate("""async () => (await axe.run(document, {
        runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa']}
    })).violations.map(v => v.id)""")
        == []
    )
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    review(page)
    page.get_by_role("button", name="Back to edit").click()
    assert len(attempts) == 1 and not submissions
    assert page.evaluate("localStorage.length + sessionStorage.length") == 0
    review(page)
    page.get_by_role("button", name="Submit to Sample Parish").click()
    expect(
        show(
            page,
            page.get_by_role(
                "heading", name="Thank you!", exact=True, include_hidden=True
            ),
        )
    ).to_be_visible()
    assert len(submissions) == 1
    assert submissions[0]["answers"]["members"]["3"]["first_name"] == "Alex"
    assert "alex@example.org" not in page.locator("body").inner_text()
    assert page.locator("#family-cancel").is_hidden()
    assert page.evaluate("localStorage.length + sessionStorage.length") == 0
    assert not failures


def test_testing_submits_without_acknowledgment_checkboxes(page, component_origin):
    """The Testing banner is the only mode notice: no entry page, no checkboxes."""
    submissions = []

    def submit(route):
        submissions.append(route.request.post_data_json)
        route.fulfill(json={"accepted": True})

    attempts = prepare(page, component_origin, testing=True, submit=submit)
    expect(page.get_by_text("Testing mode:", exact=False).first).to_be_visible()
    # The form opens straight away, without an entry page or a button to press.
    expect(page.locator("[data-step-link]").first).to_be_attached()
    expect(page.locator("#family-entry")).to_be_hidden()
    assert page.get_by_text("Test answers will not count").count() == 0
    assert page.locator("#testing-entry-ack, #testing-submit-ack").count() == 0
    assert attempts[0].post_data_json == {}
    review(page)
    assert page.locator("#family-confirmation input[type=checkbox]").count() == 0
    page.get_by_role("button", name="Submit test response").click()
    expect(page.get_by_role("heading", name="Thank you!", exact=True)).to_be_visible()
    assert "testing_acknowledged" not in submissions[0]["answers"]


def thank_you_page(page, origin, testing):
    """Submit a response and return the thank-you page's content and banner."""
    prepare(
        page,
        origin,
        testing=testing,
        submit=lambda route: route.fulfill(json={"accepted": True}),
    )
    if not testing:
        page.get_by_role("button", name="Begin reviewing").click()
    review(page)
    page.get_by_role(
        "button", name="Submit test response" if testing else "Submit to Sample Parish"
    ).click()
    expect(page.get_by_role("heading", name="Thank you!", exact=True)).to_be_visible()
    return (
        page.locator("#family-flow").inner_html(),
        page.get_by_text("Testing mode:", exact=False).count(),
    )


def test_testing_thank_you_page_matches_production(page, component_origin):
    """#289: only the Testing banner differs between the two thank-you pages."""
    testing, banner = thank_you_page(page, component_origin, True)
    assert banner == 1
    page.goto("about:blank")
    production, banner = thank_you_page(page, component_origin, False)
    assert banner == 0
    assert testing == production
    for gone in ("Test response complete", "has not been recorded", "Preview only"):
        assert gone not in testing


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
    show(page, page.get_by_label("First name (required)")).fill("My edit")
    review(page)
    page.get_by_role("button", name="Submit to Sample Parish").click()
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
    show(page, page.get_by_label("First name (required)")).fill("Private tab edit")
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
    email = show(page, page.get_by_label("Email address (optional)"))
    email.fill("invalid")
    show(page, page.get_by_label("First name (required)")).fill(
        "<img src=x onerror=alert(1)>"
    )
    expect(email).to_have_attribute("aria-invalid", "true")
    review(page)
    assert page.get_by_role("button", name="Submit to Sample Parish").count() == 0
    email.fill("valid@example.org")
    review(page)
    expect(
        show(
            page,
            page.get_by_role(
                "button", name="Submit to Sample Parish", include_hidden=True
            ),
        )
    ).to_be_visible()
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
    review(page)
    page.get_by_role("button", name="Submit to Sample Parish").click()
    if in_flight:
        expect(
            page.get_by_role("button", name="Submit to Sample Parish")
        ).to_be_disabled()
        for control in page.locator("[data-review-edit]").all():
            expect(control).to_be_disabled()
        page.clock.fast_forward(4 * 60 * 60 * 1000)
        assert pending
        pending[0].fulfill(json={"accepted": True})
    expect(
        show(
            page,
            page.get_by_role(
                "heading", name="Thank you!", exact=True, include_hidden=True
            ),
        )
    ).to_be_visible()
    page.clock.fast_forward(5 * 60 * 60 * 1000)
    expect(
        show(
            page,
            page.get_by_role(
                "heading", name="Thank you!", exact=True, include_hidden=True
            ),
        )
    ).to_be_visible()
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
    review(page)
    page.get_by_role("button", name="Submit to Sample Parish").click()
    expect(
        show(
            page,
            page.get_by_role(
                "heading", name="Thank you!", exact=True, include_hidden=True
            ),
        )
    ).to_be_visible()
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
    show(page, page.get_by_label("First name (required)")).fill("My proposed name")
    show(page, page.get_by_label("Additional information (optional)")).fill(
        "My proposed note"
    )
    review(page)
    page.get_by_role("button", name="Submit to Sample Parish").click()
    expect(page.locator("[data-conflict]")).to_have_count(2)
    review(page)
    assert page.get_by_role("button", name="Submit to Sample Parish").count() == 0
    show(
        page,
        page.get_by_role(
            "radio", name="Use updated records: Updated record", include_hidden=True
        ),
    ).check()
    show(
        page,
        page.get_by_role(
            "radio", name="Use my edit: My proposed note", include_hidden=True
        ),
    ).check()
    expect(page.get_by_label("First name (required)")).to_have_value("Updated record")
    expect(page.get_by_label("Additional information (optional)")).to_have_value(
        "My proposed note"
    )
    review(page)
    expect(
        show(
            page,
            page.get_by_role(
                "button", name="Submit to Sample Parish", include_hidden=True
            ),
        )
    ).to_be_visible()


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
    show(page, page.get_by_label("First name (required)")).fill("My edit")
    review(page)
    page.get_by_role("button", name="Submit to Sample Parish").click()
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
    review(page)
    expect(
        show(
            page,
            page.get_by_role(
                "button", name="Submit to Sample Parish", include_hidden=True
            ),
        )
    ).to_be_visible()
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
    review(page)
    page.get_by_role("button", name="Submit to Sample Parish").click()
    expect(page.locator(".family-nav")).to_be_visible()
    page.clock.fast_forward(3_700_000)
    expect(page.locator("#session-expired")).to_contain_text(
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
    review(page)
    page.get_by_role("button", name="Submit to Sample Parish").click()
    expect(page.locator("#family-flow-message")).to_contain_text("could not confirm")
    page.get_by_role("button", name="Submit to Sample Parish").click()
    if retry == "validation":
        expect(page.locator(".family-nav")).to_be_visible()
        page.clock.fast_forward(3_700_000)
    expect(page.locator("#session-expired")).to_contain_text(
        "check your last submission time"
    )
    assert (
        "Unsubmitted changes have not been saved"
        not in page.locator("main").inner_text()
    )


def test_testing_form_load_failure_offers_a_retry(page, component_origin):
    """Testing opens the form itself; a failed load shows a Try again button."""
    page.clock.install(time=NOW)
    attempts = []

    def load(route):
        """Fail the first automatic load, then return the form."""
        attempts.append(route.request)
        if len(attempts) == 1:
            route.fulfill(status=503, body="unavailable", content_type="text/plain")
        else:
            route.fulfill(json={"form": form_payload(testing=True)})

    page.route("**/family/form", load)
    page.route(
        "**/family/presence", lambda route: route.fulfill(json={"recorded": True})
    )
    page.goto(component_origin + "/family-testing")
    retry = page.get_by_role("button", name="Try again")
    expect(retry).to_be_visible()
    expect(page.locator("#family-flow-message")).to_contain_text("could not be loaded")
    retry.click()
    expect(page.locator("[data-step-link]").first).to_be_attached()
    assert len(attempts) == 2


@pytest.mark.parametrize("testing", [True, False])
def test_session_timeout_shows_one_notice_under_the_testing_banner(
    page, component_origin, testing
):
    """One red notice with one working sign-in link; Testing banner on top."""
    prepare(page, component_origin, testing=testing)
    if not testing:
        page.get_by_role("button", name="Begin reviewing").click()
    expect(page.locator("[data-step-link]").first).to_be_attached()
    page.clock.fast_forward(3_700_000)
    notice = page.locator("#session-expired")
    expect(notice).to_be_visible()
    expect(notice).to_contain_text("Your session has ended.")
    links = page.get_by_role("link", name="Sign in again")
    expect(links).to_have_count(1)
    expect(links).to_have_attribute("href", "/")
    expect(page.locator("#family-flow-message")).to_be_hidden()
    assert page.locator("#session-warning").is_hidden()
    banner = page.get_by_text("Testing mode:", exact=False)
    if testing:
        # The Testing banner comes before (above) the session notice.
        assert banner.evaluate(
            "(b, n) => Boolean(b.compareDocumentPosition(n) &"
            " Node.DOCUMENT_POSITION_FOLLOWING)",
            notice.element_handle(),
        )
        assert banner.bounding_box()["y"] < notice.bounding_box()["y"]
        # ...and above the page title, as in the Admin portal.
        title = page.locator("main h1").first
        assert banner.bounding_box()["y"] < title.bounding_box()["y"]
    else:
        expect(banner).to_have_count(0)


def test_a_server_ended_session_notice_stays_shown(page, component_origin):
    """A 403 ends the session before the timer; later timer ticks keep it."""
    prepare(
        page,
        component_origin,
        submit=lambda route: route.fulfill(status=403, body="Forbidden"),
    )
    page.get_by_role("button", name="Begin reviewing").click()
    review(page)
    page.get_by_role("button", name="Submit to Sample Parish").click()
    notice = page.locator("#session-expired")
    expect(notice).to_be_visible()
    page.clock.fast_forward(20_000)
    expect(notice).to_be_visible()
    expect(page.get_by_role("link", name="Sign in again")).to_have_count(1)
