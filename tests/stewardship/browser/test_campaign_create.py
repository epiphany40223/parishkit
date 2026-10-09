"""Create the campaign (#142): the first campaign's form and review in a browser.

The pages are the ``/campaign-create`` and ``/campaign-create-preview``
component fixtures: the form alone, and the form with its review shown in
place below it (#532). The server's admission, request and refusals are tested in
``database/test_campaign_create_postgresql.py``.
"""

import pytest

from .waits import visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

WCAG = """async () => (await axe.run(document, {
  runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa']}
})).violations.map(({id, impact}) => ({id, impact}))"""


def test_new_campaign_form_selects_every_ministry_when_enabled(page, component_origin):
    """A new campaign starts with every Ministry once its module is turned on.

    The form is marked data-new-campaign, so checking Ministry stewardship
    selects all the offered Ministries (Campaign settings, editing an existing
    campaign, selects none), and a hidden module posts nothing.
    """
    page.goto(component_origin + "/campaign-create")
    visible(page.get_by_role("heading", name="Create the campaign"))
    ministry = page.get_by_role("group", name="Ministry selections")
    financial = page.get_by_role("group", name="Financial periods and funds")
    assert not ministry.is_visible() and not financial.is_visible()
    page.get_by_label("Ministry stewardship").check()
    visible(ministry)
    included = page.get_by_label("Included Ministries")
    assert included.evaluate("select => select.selectedOptions.length") == 2
    posted = page.locator("[data-campaign-form]").evaluate(
        "form => Array.from(new FormData(form).keys())"
    )
    assert "ministry_duids" in posted and "fund_duids" not in posted
    visible(page.get_by_role("button", name="Review changes"))
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def test_create_pages_have_no_accessibility_violations(
    page, component_origin, axe_source
):
    """The form and its review pass the same WCAG checks as every Admin page."""
    for path in ("/campaign-create", "/campaign-create-preview"):
        page.goto(component_origin + path)
        page.evaluate(axe_source)
        assert page.evaluate(WCAG) == [], path


def test_review_says_it_creates_a_testing_draft(page, component_origin):
    """The in-place review names what confirming does; its button says Create.

    Editing the form after the review withdraws it in place, as on Campaign
    settings: Create the campaign is removed, and the review stays where it
    was, so nothing moves under the pointer.
    """
    page.goto(component_origin + "/campaign-create-preview")
    review = page.locator("#settings-review [data-review-of='settings-form']")
    visible(review.get_by_text("This creates the campaign as a draft in Testing mode."))
    visible(review.get_by_text("Pages and emails start with the default text."))
    visible(review.get_by_text("Nothing is saved until you choose Create the"))
    create = page.get_by_role("button", name="Create the campaign")
    visible(create)
    assert not page.get_by_role("button", name="Apply changes").count()
    top = page.locator("#settings-review").evaluate(
        "node => node.getBoundingClientRect().top"
    )
    page.get_by_label("Campaign name").fill("Annual stewardship")
    visible(page.get_by_text("Choose Review changes again"))
    assert not create.count()
    assert (
        page.locator("#settings-review").evaluate(
            "node => node.getBoundingClientRect().top"
        )
        == top
    )
