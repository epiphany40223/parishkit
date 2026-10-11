"""The Reminder WorkGroup setting page (#861) in a real browser."""

import pytest

from .reminder_workgroup_components import EDITOR, NAME, PENDING, REVIEW
from .test_in_place import MARK, MARKED, count_requests
from .test_settings_in_place import (
    DIGEST,
    FOCUSED,
    answer_reviews,
    current_step,
    expect_review,
)
from .waits import has_attribute, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)
PATHS = (
    "/reminder-workgroup-found",
    "/reminder-workgroup-missing",
    "/reminder-workgroup-off",
    "/reminder-workgroup-preview",
)


@pytest.mark.parametrize("width", [320, 1280])
def test_reminder_workgroup_pages_are_accessible_and_fit(
    page, component_origin, axe_source, width
):
    """Every state and the review pass axe and fit a phone without scrolling."""
    page.set_viewport_size({"width": width, "height": 900})
    for path in PATHS:
        failures = []
        page.on("pageerror", lambda error, seen=failures: seen.append(str(error)))
        page.goto(component_origin + path)
        assert not failures
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.evaluate(axe_source)
        assert (
            page.evaluate("""async () => (await axe.run(document, {
            runOnly: {type: 'tag', values: ['wcag2a','wcag2aa','wcag21aa','wcag22aa']}
        })).violations.map(({id,impact}) => ({id,impact}))""")
            == []
        )


def test_the_field_explains_itself_and_each_state_says_what_happens(
    page, component_origin
):
    """The name field has its plain-language help; each state is stated."""
    page.goto(component_origin + "/reminder-workgroup-found")
    field = page.get_by_label("ParishSoft Family WorkGroup")
    assert field.input_value() == "Active: Stewardship 2027"
    visible(page.get_by_text("Families in that WorkGroup get no Reminder emails"))
    visible(page.get_by_text("found 2 Families in"))
    page.goto(component_origin + "/reminder-workgroup-missing")
    visible(page.locator("[data-workgroup-status=missing]"))
    page.goto(component_origin + "/reminder-workgroup-off")
    assert page.get_by_label("ParishSoft Family WorkGroup").input_value() == ""
    visible(page.locator("[data-workgroup-status=off]"))
    page.goto(component_origin + "/reminder-workgroup-preview")
    visible(page.get_by_role("button", name="Apply changes"))


# The settings form's top edge in the document: focus may scroll the window
# to the review below it, but nothing may move the form within the page.
FORM_PLACE = (
    "document.getElementById('settings-form').getBoundingClientRect().top"
    " + window.scrollY"
)
HINT = "Change the WorkGroup name to review it."


def test_review_apply_and_status_happen_in_place(page, component_origin):
    """Review changes waits for a change; Review shows the change under the
    form and takes focus; Apply shows its status there, and once it is
    applied the page refreshes itself quietly: the form takes the applied
    version and "Now" says the name takes effect at the next refresh.
    Nothing reloads, the form never moves and keeps the typed name (#750)."""
    reviews = answer_reviews(page, component_origin, REVIEW, address=EDITOR)
    posts = count_requests(page, "POST", EDITOR)
    page.goto(component_origin + EDITOR)
    page.evaluate(MARK)
    expect_review(page, enabled=False, hint=HINT)
    field = page.get_by_label("ParishSoft Family WorkGroup")
    field.fill(NAME)
    expect_review(page, enabled=True, hint=HINT)
    top = page.evaluate(FORM_PLACE)
    page.get_by_role("button", name="Review changes").click()
    visible(page.get_by_role("heading", name="Review your changes"))
    assert len(reviews) == 1 and "name=Active" in reviews[0]
    assert page.evaluate(FOCUSED, "#settings-review-title")
    panel = page.locator("#settings-review")
    assert NAME in panel.inner_text()
    assert current_step(page) == "Review"
    assert page.url == component_origin + EDITOR
    assert page.evaluate(FORM_PLACE) == top
    page.get_by_role("button", name="Apply changes").click()
    visible(panel.get_by_role("heading", name="Change status"))
    assert current_step(page) == "Apply"
    # The status settles as Applied and the page refreshes itself quietly.
    visible(panel.get_by_role("heading", name="Applied: your change is saved"))
    has_attribute(page.locator(DIGEST), "value", "b" * 64)
    visible(page.locator("#workgroup-status [data-workgroup-status=pending]"))
    assert field.input_value() == NAME
    assert PENDING.split("?")[1] in page.url
    assert page.evaluate(FORM_PLACE) == top
    assert page.evaluate(MARKED) == "kept"
    assert len(posts) == 2


def test_editing_after_a_review_withdraws_it(page, component_origin):
    """Typing again after a review withdraws it, so Apply can only apply
    the name shown; the review stays in place, greyed."""
    answer_reviews(page, component_origin, REVIEW, address=EDITOR)
    page.goto(component_origin + EDITOR)
    field = page.get_by_label("ParishSoft Family WorkGroup")
    field.fill(NAME)
    page.get_by_role("button", name="Review changes").click()
    visible(page.get_by_role("heading", name="Review your changes"))
    field.fill(NAME + " again")
    visible(page.locator("#settings-review section[data-review-stale]"))
    assert page.get_by_role("button", name="Apply changes").count() == 0
