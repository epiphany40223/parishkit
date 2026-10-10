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
    LEADERS,
    LEADERS_PENDING,
    LEADERS_REVIEW,
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
    # The Ministry leader roles sit in that group too (#922), ticked with
    # the roles in effect.
    roles = page.get_by_role("group", name="Ministry leader roles")
    visible(roles)
    assert roles.get_by_label("Chairperson").is_checked()
    assert roles.get_by_label("Staff").is_checked()
    assert not roles.get_by_label("Member").is_checked()


def test_a_live_campaigns_leader_roles_are_reviewed_and_applied_in_place(
    page, component_origin
):
    """A live campaign's one editable setting besides its Ministries, the
    Ministry leader roles (#922), has its own form: Review shows the change
    under it and takes focus, and Apply shows the change's status there.
    Nothing reloads and the ticks stay as the reader left them."""
    reviews = answer_reviews(page, component_origin, LEADERS_REVIEW, address=LEADERS)
    page.goto(component_origin + LEADERS)
    page.evaluate(MARK)
    roles = page.get_by_role("group", name="Ministry leader roles")
    visible(roles)
    # Its help says what the roles do, in plain words (behind its tip).
    assert "sees the reports and follow-up of each Ministry" in (
        page.locator("#leader-roles").text_content()
    )
    # The other settings stay read-only.
    assert page.locator("fieldset[disabled]").count() == 1
    roles.get_by_label("Staff").uncheck()
    page.locator("#leader-roles").get_by_role("button", name="Review changes").click()
    visible(page.get_by_role("heading", name="Review your changes"))
    assert len(reviews) == 1
    assert "editor=leader_roles" in reviews[0]
    assert "ministry_leader_roles=Chairperson" in reviews[0]
    assert "ministry_leader_roles=Staff" not in reviews[0]
    assert page.evaluate(FOCUSED, "#settings-review-title")
    panel = page.locator("#leader-roles #settings-review")
    assert "Chairperson, Staff" in panel.inner_text()
    assert current_step(page) == "Review"
    page.get_by_role("button", name="Apply changes").click()
    visible(panel.get_by_role("heading", name="Change status"))
    assert page.url == f"{component_origin}{LEADERS_PENDING}#settings-review"
    assert not roles.get_by_label("Staff").is_checked()
    assert page.evaluate(MARKED) == "kept"
