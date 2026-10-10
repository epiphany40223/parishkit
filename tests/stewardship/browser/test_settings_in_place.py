"""A settings change is reviewed, applied and followed in place (#532).

Parish settings is served by ``settings_components``: a page route answers
the form's Review POST with the review (or a refusal), and passes Apply's
POST on to the fixture server, which redirects back to the page naming the
change. Each test sets a mark outside every region, which only a full page
load could lose.
"""

import pytest

from .settings_components import (
    CAMPAIGN,
    CAMPAIGN_REVIEW,
    PENDING,
    REFUSED,
    REQUEST,
    REVIEW,
    SETTINGS,
)
from .test_in_place import MARK, MARKED, count_requests
from .waits import has_attribute, has_text, hidden, recorded, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

FOCUSED = "selector => document.activeElement === document.querySelector(selector)"
DIGEST = "#settings-form input[name=base_digest]"
# The settings form's top edge in the viewport, to show it did not move.
FORM_TOP = "document.getElementById('settings-form').getBoundingClientRect().top"


def answer_reviews(page, origin, fixture=REVIEW, status=200, address=SETTINGS):
    """Answer ``address``'s Review POSTs with ``fixture``; Apply goes through.

    Returns the list of Review POST bodies answered.
    """
    reviews = []

    def answer(route):
        """Serve ``fixture`` for a Review; let loads and Apply through."""
        request = route.request
        if request.method != "POST" or "action=preview" not in (
            request.post_data or ""
        ):
            route.continue_()
            return
        reviews.append(request.post_data)
        fetched = route.fetch(url=origin + fixture, method="GET")
        route.fulfill(response=fetched, status=status)

    page.route(lambda url: url.split("#")[0] == origin + address, answer)
    return reviews


RESTORE = "window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted: true}))"
HINT = "#settings-form-unchanged-hint"


def expect_review(page, *, enabled, hint="Change a setting to review it."):
    """Review changes is available exactly when the form differs from what is
    saved (#921); otherwise its hint, which always keeps its space, is shown
    and describes it."""
    from playwright.sync_api import expect

    button = page.get_by_role("button", name="Review changes")
    line = page.locator(HINT)
    expect(line).to_have_text(hint)
    if enabled:
        expect(button).to_be_enabled()
        expect(line).to_have_css("visibility", "hidden")
        expect(button).not_to_have_attribute("aria-describedby", HINT[1:])
    else:
        expect(button).to_be_disabled()
        expect(line).to_have_css("visibility", "visible")
        expect(button).to_have_accessible_description(hint)


def current_step(page):
    """The step indicator's current step label."""
    return page.locator("#flow-steps [aria-current=step] .flow-step-label").inner_text()


def review(page, component_origin):
    """Open Parish settings, rename the parish and review the change."""
    page.goto(component_origin + SETTINGS)
    page.evaluate(MARK)
    page.get_by_label("Parish name").fill("Renamed Parish")
    page.get_by_role("button", name="Review changes").click()
    visible(page.get_by_role("heading", name="Review your changes"))


def test_review_apply_and_status_happen_in_place(page, component_origin):
    """Review shows the change under the form and takes focus; Apply shows
    the change's live status there; once it settles, the page refreshes
    itself quietly so the form is at the applied version. Nothing reloads,
    the reader's typing stays, and the indicator follows each step."""
    reviews = answer_reviews(page, component_origin)
    posts = count_requests(page, "POST", SETTINGS)
    review(page, component_origin)
    assert len(reviews) == 1 and "name=Renamed+Parish" in reviews[0]
    assert page.evaluate(FOCUSED, "#settings-review-title")
    panel = page.locator("#settings-review")
    assert "Sample Parish" in panel.inner_text()
    assert "This timezone change is prospective." in panel.inner_text()
    has_text(
        page.get_by_role("status").filter(has_text="Review ready."), "Review ready."
    )
    assert current_step(page) == "Review"
    assert page.url == component_origin + SETTINGS
    page.get_by_role("button", name="Apply changes").click()
    # The status is shown where the review was, and the address names the
    # change (a reload shows its status again, never re-posts).
    visible(panel.get_by_role("heading", name="Change status"))
    assert page.evaluate(FOCUSED, "#settings-status-title")
    assert current_step(page) == "Apply"
    assert page.url == f"{component_origin}{PENDING}#settings-review"
    # Change status answers Applied, and the follow-up refreshes the page in
    # place: the form takes the applied version, keeping the typed name.
    visible(panel.get_by_role("heading", name="Applied: your change is saved"))
    has_attribute(page.locator(DIGEST), "value", "b" * 64)
    assert page.get_by_label("Parish name").input_value() == "Renamed Parish"
    # The applied values are now the saved ones, so there is nothing left
    # to review until the reader changes something again (#921).
    expect_review(page, enabled=False)
    page.get_by_label("Parish name").fill("Renamed once more")
    expect_review(page, enabled=True)
    assert page.evaluate(MARKED) == "kept"
    assert str(REQUEST) in page.url and "settled=1" in page.url
    assert len(posts) == 2


def test_editing_after_a_review_withdraws_it(page, component_origin):
    """Apply always applies the reviewed values, so changing the form after a
    review takes its Apply away and says to review again. The review stays
    in place, greyed out, so the form never moves under the reader (#736)."""
    answer_reviews(page, component_origin)
    review(page, component_origin)
    # A short window scrolled to the bottom: a review that shrank would
    # pull the page, and the form with it, down under the caret.
    page.set_viewport_size({"width": 800, "height": 400})
    page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
    before = page.evaluate(FORM_TOP)
    page.get_by_label("Parish name").evaluate(
        """field => {
            field.value = "Renamed again";
            field.dispatchEvent(new Event("input", {bubbles: true}));
        }"""
    )
    visible(page.get_by_text("Choose Review changes again"))
    assert page.evaluate(FORM_TOP) == before
    assert page.locator("#settings-review [data-review-stale]").count() == 1
    assert page.locator("#settings-review [data-review-of]").count() == 0
    assert page.get_by_role("button", name="Apply changes").count() == 0
    # Review again brings a fresh review, with its Apply.
    page.get_by_role("button", name="Review changes").click()
    visible(page.get_by_role("button", name="Apply changes"))
    assert page.locator("#settings-review [data-review-stale]").count() == 0
    assert page.evaluate(MARKED) == "kept"


def test_a_refused_review_is_explained_in_place(page, component_origin):
    """A refusal is the page again: its summary appears in the review region
    and takes focus, linked to the field, while the form keeps the typing."""
    answer_reviews(page, component_origin, REFUSED, status=400)
    page.goto(component_origin + SETTINGS)
    page.evaluate(MARK)
    page.get_by_label("Parish telephone").fill("123")
    page.get_by_role("button", name="Review changes").click()
    summary = page.locator("#settings-review [data-error-summary]")
    visible(summary)
    assert page.evaluate(FOCUSED, "#settings-review [data-error-summary]")
    link = summary.get_by_role("link", name="Parish telephone: Enter a ten-digit")
    link.click()
    assert page.evaluate("document.activeElement.id") == "id_phone"
    assert page.get_by_label("Parish telephone").input_value() == "123"
    assert page.evaluate(MARKED) == "kept"
    assert current_step(page) == "Make changes"


def test_an_edit_while_review_is_in_flight_withdraws_its_review(page, component_origin):
    """A review answered after the reader edited the form again does not
    show what the form now holds, so it is withdrawn as it arrives, even
    when Enter was pressed again while the Review ran (a repeat that is
    ignored, so it must not forget the edit)."""
    held = []
    page.route(
        lambda url: url.split("#")[0] == component_origin + SETTINGS,
        lambda route: (
            held.append(route) if route.request.method == "POST" else route.continue_()
        ),
    )
    page.goto(component_origin + SETTINGS)
    page.get_by_label("Parish name").fill("Renamed Parish")
    page.get_by_role("button", name="Review changes").click()
    recorded(page, held, 1)
    page.get_by_label("Parish name").fill("Renamed during review")
    page.get_by_label("Parish name").press("Enter")
    fetched = held[0].fetch(url=component_origin + REVIEW, method="GET")
    held[0].fulfill(response=fetched)
    visible(page.get_by_text("Choose Review changes again"))
    assert page.get_by_role("button", name="Apply changes").count() == 0
    assert page.get_by_label("Parish name").input_value() == "Renamed during review"
    assert len(held) == 1


def test_a_review_that_leads_to_another_page_drops_the_region_fragment(
    page, component_origin
):
    """A Review the server redirects to another page (Campaign settings'
    dates-only change goes to Dates and mail schedules) loads that page
    without this page's region fragment, which would name nothing there."""

    def elsewhere(route):
        """Send Review's POST to a fixture path that redirects elsewhere."""
        if route.request.method != "POST":
            route.continue_()
            return
        # A page without the review region (the in-place form fixture's other
        # page has only its own region).
        route.continue_(url=component_origin + "/settings-in-place-elsewhere")

    page.route(lambda url: url.split("#")[0] == component_origin + SETTINGS, elsewhere)
    page.goto(component_origin + SETTINGS)
    page.get_by_label("Parish name").fill("Renamed Parish")
    page.get_by_role("button", name="Review changes").click()
    page.wait_for_url(component_origin + "/in-place-other")


def test_campaign_settings_reviews_in_place_and_keeps_its_module_scripts(
    page, component_origin
):
    """Campaign settings' review fills the region under its form, the form
    (and the scripts bound to it, such as the module groups) stays, and the
    step indicator follows."""
    answer_reviews(page, component_origin, CAMPAIGN_REVIEW, address=CAMPAIGN)
    page.goto(component_origin + CAMPAIGN)
    page.evaluate(MARK)
    page.get_by_label("Campaign name").fill("Renamed campaign")
    page.get_by_role("button", name="Review changes").click()
    visible(page.get_by_role("heading", name="Review your changes"))
    assert page.evaluate(FOCUSED, "#settings-review-title")
    assert current_step(page) == "Review"
    assert "Renamed campaign" in page.locator("#settings-review").inner_text()
    assert page.get_by_label("Campaign name").input_value() == "Renamed campaign"
    assert page.evaluate(MARKED) == "kept"
    # The module script still answers the form it was bound to; ticking it
    # is an edit, so the review is withdrawn.
    group = page.locator("#ministry-selections")
    hidden(group)
    page.get_by_label("Ministry stewardship").check()
    visible(group)
    visible(page.get_by_text("Choose Review changes again"))


def test_parish_settings_review_waits_for_a_change(page, component_origin):
    """Review changes is unavailable until the form differs from the saved
    settings, available after an edit, unavailable again once the edit is
    undone, and checked again when a page restored from history is shown
    with values that came back without events (#921). Its hint keeps its
    space, so the form never moves."""
    reviews = answer_reviews(page, component_origin)
    page.goto(component_origin + SETTINGS)
    expect_review(page, enabled=False)
    before = page.evaluate(FORM_TOP)
    name = page.get_by_label("Parish name")
    # Pressing Enter in a field cannot submit an unchanged form either.
    name.press("Enter")
    name.fill("Renamed Parish")
    expect_review(page, enabled=True)
    assert page.evaluate(FORM_TOP) == before
    name.fill("Sample Parish")
    expect_review(page, enabled=False)
    # Spaces the server trims are no change; a choice is.
    name.fill("Sample Parish ")
    expect_review(page, enabled=False)
    page.get_by_label("Parish timezone").select_option("America/Chicago")
    expect_review(page, enabled=True)
    page.get_by_label("Parish timezone").select_option("America/New_York")
    expect_review(page, enabled=False)
    # A restore without events, then pageshow: the gate follows the values.
    name.evaluate("field => { field.value = 'Restored name'; }")
    page.evaluate(RESTORE)
    expect_review(page, enabled=True)
    name.evaluate("field => { field.value = 'Sample Parish'; }")
    page.evaluate(RESTORE)
    expect_review(page, enabled=False)
    assert page.evaluate(FORM_TOP) == before
    assert reviews == []


def test_campaign_settings_review_waits_for_a_change(page, component_origin):
    """Campaign settings follows the same rule: a text edit or a module tick
    makes Review available, undoing it makes it unavailable again, and a
    restored tick is noticed on pageshow (#921)."""
    page.goto(component_origin + CAMPAIGN)
    expect_review(page, enabled=False)
    name = page.get_by_label("Campaign name")
    name.fill("Renamed campaign")
    expect_review(page, enabled=True)
    name.fill("Sample campaign")
    expect_review(page, enabled=False)
    module = page.get_by_label("Ministry stewardship")
    module.check()
    expect_review(page, enabled=True)
    module.uncheck()
    expect_review(page, enabled=False)
    module.evaluate("box => { box.checked = true; }")
    page.evaluate(RESTORE)
    expect_review(page, enabled=True)
