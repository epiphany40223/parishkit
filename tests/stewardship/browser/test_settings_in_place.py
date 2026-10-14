"""A settings change is reviewed, applied and followed in place (#532).

Parish settings is served by ``settings_components`` (Share options reviews
in the review dialog: test_share_options_dialog.py): a page route answers
the form's Review POST with the review (or a refusal), and passes Apply's
POST on to the fixture server, which redirects back to the page naming the
change. Each test sets a mark outside every region, which only a full page
load could lose.
"""

import pytest

from .settings_components import (
    PENDING,
    REFUSED,
    REQUEST,
    REVIEW,
    SETTINGS,
    STATUS,
)
from .test_in_place import MARK, MARKED, count_requests
from .waits import has_attribute, has_text, recorded, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

FOCUSED = "selector => document.activeElement === document.querySelector(selector)"
DIGEST = "#settings-form input[name=base_digest]"
# The settings form's top edge in the viewport, to show it did not move.
FORM_TOP = "document.getElementById('settings-form').getBoundingClientRect().top"
# Its top edge in the document: focus may scroll the window to a review
# below it, but nothing may move the form within the page.
FORM_PLACE = f"{FORM_TOP} + window.scrollY"


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
    assert page.evaluate(FOCUSED, "#settings-review-status-title")
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
    assert page.evaluate(FORM_TOP) == pytest.approx(before, abs=0.5)
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


def hold_status(page, component_origin):
    """Hold Change status's polls; returns the list of held routes."""
    held = []
    page.route(
        lambda url: url.split("?")[0] == component_origin + STATUS,
        lambda route: held.append(route),
    )
    return held


def to_the_foot(page, height=400):
    """Scroll to the foot of a short window; returns the form's top edge."""
    page.set_viewport_size({"width": 800, "height": height})
    page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
    # Only a page scrolled to its foot can be pulled down by a shorter one.
    assert page.evaluate("scrollY") > 0
    return page.evaluate(FORM_TOP)


def test_a_shorter_status_never_moves_the_form(page, component_origin):
    """A reader at the foot of the page sees nothing move when Apply swaps
    the review for Change status, when Applied (shorter than the running
    indicator) replaces it, or when the quiet refresh redraws the region:
    the review region keeps the height their place needs (data-keep-height),
    so the page never gets shorter under them (#736)."""
    answer_reviews(page, component_origin)
    held = hold_status(page, component_origin)
    review(page, component_origin)
    # Tall enough to show the whole review region, so moving focus to the
    # status's heading has no reason to scroll.
    before = to_the_foot(page, 600)
    page.get_by_role("button", name="Apply changes").click()
    visible(page.get_by_role("heading", name="Change status"))
    recorded(page, held, 1)
    assert page.evaluate(FORM_TOP) == pytest.approx(before, abs=0.5)
    held[0].continue_()
    visible(page.get_by_role("heading", name="Applied: your change is saved"))
    has_attribute(page.locator(DIGEST), "value", "b" * 64)
    assert page.evaluate(FORM_TOP) == pytest.approx(before, abs=0.5)
    assert page.evaluate(MARKED) == "kept"


def test_focus_brings_the_status_into_view_on_a_short_window(page, component_origin):
    """On a window too short to show the whole review region, Apply from
    the foot of the page moves focus to Change status's heading, which may
    scroll the page so the status is in view (#736); the held height only
    keeps the page from being pulled down under the reader."""
    answer_reviews(page, component_origin)
    hold_status(page, component_origin)
    review(page, component_origin)
    to_the_foot(page, 400)
    page.get_by_role("button", name="Apply changes").click()
    heading = page.get_by_role("heading", name="Change status")
    visible(heading)
    assert page.evaluate(FOCUSED, "#settings-review-status-title")
    from playwright.sync_api import expect

    expect(heading).to_be_in_viewport()
    assert page.evaluate(MARKED) == "kept"


def test_a_status_loaded_with_the_page_never_moves_the_form(page, component_origin):
    """A page loaded with Change status already in its region (a reload of
    the change's address) holds its place the same way when Applied
    replaces the running indicator (#736)."""
    held = hold_status(page, component_origin)
    page.goto(component_origin + PENDING)
    visible(page.get_by_role("heading", name="Change status"))
    recorded(page, held, 1)
    before = to_the_foot(page)
    held[0].continue_()
    visible(page.get_by_role("heading", name="Applied: your change is saved"))
    has_attribute(page.locator(DIGEST), "value", "b" * 64)
    assert page.evaluate(FORM_TOP) == pytest.approx(before, abs=0.5)


def test_a_long_review_leaves_no_gap_above_the_foot(page, component_origin):
    """The review region holds only what the reader's place needs: a long
    review followed by Applied, read from the top of the page, leaves no
    gap the height of the review below the status (#736)."""
    answer_reviews(page, component_origin)
    review(page, component_origin)
    page.evaluate(
        """() => {
            const tall = document.createElement("div");
            tall.style.height = "2000px";
            document.querySelector("[data-review-of]").prepend(tall);
            window.scrollTo(0, 0);
        }"""
    )
    # Apply is pressed without scrolling to it, so the reader stays at the top.
    page.get_by_role("button", name="Apply changes").evaluate(
        "button => button.click()"
    )
    visible(page.get_by_role("heading", name="Applied: your change is saved"))
    has_attribute(page.locator(DIGEST), "value", "b" * 64)
    height = page.evaluate(
        "document.getElementById('settings-review').getBoundingClientRect().height"
    )
    assert height < 1000
