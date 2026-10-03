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


def current_page(page):
    """The key of the form page the Family is looking at."""
    return page.locator("[data-page]:not([hidden])").get_attribute("data-page")


def advance(page, *, required=True):
    """Select Next once and return whether the form moved to the following page.

    Next checks the page being left. By default a blocked Next fails the test;
    with ``required=False`` the caller handles it.
    """
    before = current_page(page)
    page.locator("[data-page-next]").dispatch_event("click")
    moved = current_page(page) != before
    assert moved or not required, f"Next did not leave the {before} page"
    return moved


def locked(link):
    """Whether a step-bar segment is not yet available (#330)."""
    return link.get_attribute("aria-disabled") == "true"


def show(page, locator):
    """Open the Family form page that holds ``locator`` and return it.

    The form shows one page at a time; other pages stay in the DOM but hidden.
    This uses the page's own step-bar segment, so it follows the same code path
    as a Family jumping to a page. A Family cannot jump past the furthest page
    reached (#330), so a later page is reached with Next first, as a Family
    would. It dispatches the clicks directly: these tests exercise their own
    behavior, and real navigation is covered in test_family_pages.py.
    """
    key = locator.first.evaluate("e => e.closest('[data-page]')?.dataset.page || ''")
    if key and not locator.first.is_visible():
        link = page.locator(f'[data-step-link="{key}"]')
        while locked(link):
            advance(page)
        link.dispatch_event("click")
    return locator


def visit_every_page(page, *, required=True):
    """Open each form page once, as a Family must before Review.

    Segments already available are opened directly; the rest are reached with
    Next from the page before, since the step bar never jumps ahead (#330).
    Returns whether every page was reached: with ``required=False``, a page
    whose own check blocks Next stops the walk there instead of failing.
    """
    links = page.locator("[data-step-link]")
    # The step bar is built once the form loads; count only after it exists.
    expect(links.first).to_be_attached()
    for index in range(links.count()):
        if not locked(links.nth(index)):
            links.nth(index).dispatch_event("click")
        elif not advance(page, required=required):
            return False
    return True


def review(page):
    """Go to Review as a Family would, and select Review response.

    A Family reaches Review only through every page (#330), so Next checks any
    page not yet passed. When one of those has a problem, the Family stops on
    that page with its errors shown, exactly as Next shows them, instead of
    reaching Review; tests of Review-time checks then assert on those errors.
    Returns whether Review response was selected, so such a test can say
    which it expects.
    """
    if not visit_every_page(page, required=False):
        return False
    page.get_by_role("button", name="Review response").click()
    return True


def unseen(locator):
    """True when sighted users cannot see ``locator``: hidden, or 1px and
    clipped for screen readers only (#295)."""
    return locator.evaluate("e => e.hidden || e.getBoundingClientRect().width <= 1")


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


def prepare(page, origin, *, testing=False, submit=None, form=None, load=None):
    """Capture boundary traffic; presence is explicitly answer-free and separate.

    The page loads the form as soon as it opens (#466), so every route must be
    in place before ``goto``: pass a fixed ``form`` or a ``load`` handler here
    rather than routing ``/family/form`` afterwards.
    """
    page.clock.install(time=NOW)
    attempts = []

    def begin(route):
        """Record the automatic form load, then answer it."""
        attempts.append(route.request)
        if load:
            load(route)
        else:
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
    # Review shows the birth date in the parish format, not raw ISO.
    main = page.locator("main").inner_text()
    assert "January 1, 1960" in main and "1960-01-01" not in main
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


def test_production_opens_the_form_without_a_click(page, component_origin):
    """#466: a Family arriving from an invitation sees the form, not a button."""
    attempts = prepare(page, component_origin)
    expect(page.locator("[data-step-link]").first).to_be_attached()
    expect(page.get_by_label("First name (required)")).to_have_value("Alex")
    # The entry panel is only the failure fallback; it never showed here.
    expect(page.locator("#family-entry")).to_be_hidden()
    assert page.get_by_role("button", name="Try again").count() == 0
    assert page.get_by_text("Testing mode:", exact=False).count() == 0
    assert [attempt.post_data_json for attempt in attempts] == [{}]


def thank_you_page(page, origin, testing):
    """Submit a response and return the thank-you page's content and banner."""
    prepare(
        page,
        origin,
        testing=testing,
        submit=lambda route: route.fulfill(json={"accepted": True}),
    )
    review(page)
    page.get_by_role(
        "button", name="Submit test response" if testing else "Submit to Sample Parish"
    ).click()
    expect(page.get_by_role("heading", name="Thank you!", exact=True)).to_be_visible()
    banner = page.get_by_text("Testing mode:", exact=False)
    if testing:
        expect(banner).to_be_visible()
    else:
        expect(banner).to_have_count(0)
    return page.locator("#family-flow").inner_html()


def test_testing_thank_you_page_matches_production(page, component_origin):
    """#289: only the Testing banner differs between the two thank-you pages."""
    testing = thank_you_page(page, component_origin, True)
    page.goto("about:blank")
    production = thank_you_page(page, component_origin, False)
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
    email = show(page, page.get_by_label("Email address (optional)"))
    email.fill("invalid")
    show(page, page.get_by_label("First name (required)")).fill(
        "<img src=x onerror=alert(1)>"
    )
    expect(email).to_have_attribute("aria-invalid", "true")
    assert not review(page)
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
    form = form_payload()
    form["additional_enabled"] = False
    form["additional_information"] = "Old text that is no longer enabled"
    submissions = []
    prepare(page, component_origin, form=form)

    def submit(route):
        submissions.append(route.request.post_data_json)
        route.fulfill(json={"accepted": True})

    page.route("**/family/submit", submit)
    # The form loads on its own (#466); wait for it before checking absence.
    expect(page.locator("[data-step-link]").first).to_be_attached()
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


@pytest.mark.parametrize("testing", [True, False])
def test_form_load_failure_offers_a_retry(page, component_origin, testing):
    """Both modes open the form themselves; a failed load shows Try again (#466)."""
    attempts = []

    def load(route):
        """Fail the first automatic load, then return the form."""
        attempts.append(route.request)
        if len(attempts) == 1:
            route.fulfill(status=503, body="unavailable", content_type="text/plain")
        else:
            route.fulfill(json={"form": form_payload(testing=testing)})

    prepare(page, component_origin, testing=testing, load=load)
    retry = page.get_by_role("button", name="Try again")
    expect(retry).to_be_visible()
    expect(page.locator("#family-flow-message")).to_contain_text("could not be loaded")
    retry.click()
    expect(page.locator("[data-step-link]").first).to_be_attached()
    expect(page.locator("#family-entry")).to_be_hidden()
    assert len(attempts) == 2


@pytest.mark.parametrize("testing", [True, False])
def test_session_timeout_shows_one_notice_under_the_testing_banner(
    page, component_origin, testing
):
    """One red notice with one working sign-in link; Testing banner on top."""
    prepare(page, component_origin, testing=testing)
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
    review(page)
    page.get_by_role("button", name="Submit to Sample Parish").click()
    notice = page.locator("#session-expired")
    expect(notice).to_be_visible()
    page.clock.fast_forward(20_000)
    expect(notice).to_be_visible()
    expect(page.get_by_role("link", name="Sign in again")).to_have_count(1)


def test_temporary_outage_on_submit_keeps_answers_and_is_not_uncertain(
    page, component_origin
):
    """#315 M3: a JSON 503 refusal is definite, not a possibly lost submission."""
    prepare(
        page,
        component_origin,
        submit=lambda route: route.fulfill(
            status=503, json={"error": "temporarily_unavailable"}
        ),
    )
    show(page, page.get_by_label("First name (required)")).fill("My edit")
    review(page)
    page.get_by_role("button", name="Submit to Sample Parish").click()
    message = page.locator("#family-flow-message")
    expect(message).to_contain_text("could not be submitted")
    expect(message).to_contain_text("Your edits remain in this tab")
    assert "could not confirm" not in message.inner_text()
    assert "My edit" in page.locator("main").inner_text()
    # A later expiry must not suggest that the refused Submit may have landed.
    page.clock.fast_forward(3_700_000)
    expect(page.locator("#session-expired")).to_contain_text(
        "Unsubmitted changes have not been saved"
    )
    assert "check your last submission time" not in page.locator("main").inner_text()


def test_form_opened_in_second_tab_keeps_first_tab_edits(page, component_origin):
    """#315 M2: a replaced baseline fetches a fresh form instead of losing edits.

    A small fake server models the real rule: each form load issues a new
    baseline and replaces the session's earlier one, and Submit answers
    ``reload_required`` for any baseline that is not the latest.
    """
    issued, submissions = [], []

    def load(route):
        """Issue a fresh baseline, replacing every earlier one."""
        form = form_payload()
        issued.append(form["baseline"])
        route.fulfill(json={"form": form})

    def submit(route):
        """Accept only the session's current baseline."""
        body = route.request.post_data_json
        submissions.append(body)
        if body["baseline"] != issued[-1]:
            route.fulfill(status=409, json={"error": "reload_required"})
        else:
            route.fulfill(json={"accepted": True})

    context = page.context
    context.route("**/family/form", load)
    context.route("**/family/submit", submit)
    context.route(
        "**/family/presence", lambda route: route.fulfill(json={"recorded": True})
    )
    page.clock.install(time=NOW)
    page.goto(component_origin + "/family")
    show(page, page.get_by_label("First name (required)")).fill("My edit")
    # The Family opens the form again in a second tab of the same session.
    second = context.new_page()
    second.clock.install(time=NOW)
    second.goto(component_origin + "/family")
    expect(second.locator("[data-step-link]").first).to_be_attached()
    assert len(issued) == 2
    second.close()
    review(page)
    page.get_by_role("button", name="Submit to Sample Parish").click()
    expect(page.locator("#family-flow-message")).to_contain_text(
        "Your edits are preserved"
    )
    assert len(issued) == 3
    expect(show(page, page.get_by_label("First name (required)"))).to_have_value(
        "My edit"
    )
    review(page)
    page.get_by_role("button", name="Submit to Sample Parish").click()
    expect(page.get_by_role("heading", name="Thank you!", exact=True)).to_be_visible()
    assert [body["baseline"] for body in submissions] == issued[::2]
    assert submissions[-1]["answers"]["members"]["3"]["first_name"] == "My edit"


def test_replaced_baseline_with_changed_records_asks_for_a_choice(
    page, component_origin
):
    """#315 M2: a fresh form after reload_required merges like review_required.

    The records changed while this tab's baseline was replaced: an edited
    field needs the Family's explicit choice and an unedited one adopts the
    new value.
    """
    loads = []

    def load(route):
        """First the original form, then one with changed source names."""
        form = form_payload()
        if loads:
            member_field(form, "first_name")["value"] = "New source first"
            member_field(form, "last_name")["value"] = "New source last"
        loads.append(form["baseline"])
        route.fulfill(json={"form": form})

    prepare(
        page,
        component_origin,
        submit=lambda route: route.fulfill(
            status=409, json={"error": "reload_required"}
        ),
        load=load,
    )
    show(page, page.get_by_label("First name (required)")).fill("My edit")
    review(page)
    page.get_by_role("button", name="Submit to Sample Parish").click()
    expect(page.locator("#family-flow-message")).to_contain_text(
        "Your edits are preserved"
    )
    assert len(loads) == 2
    expect(page.locator('[data-conflict="members.3.first_name"]')).to_be_visible()
    expect(page.get_by_label("Last name (required)")).to_have_value("New source last")
