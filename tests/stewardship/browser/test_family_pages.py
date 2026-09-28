"""The paged Family form: navigation, per-page checks and conditional fields."""

import os
from pathlib import Path

import pytest

from ..test_financial_answers import CHECK
from .test_family_financial import financial_form
from .test_family_ministry import begin
from .test_family_response import expect, review, show

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

AXE = """async () => (await axe.run(document, {
  runOnly: {type: 'tag', values: ['wcag2a','wcag2aa','wcag21aa','wcag22aa']}
})).violations.map(v => v.id)"""


def paged_form():
    """Every module on, with a surname and a one-person ParishSoft mailing name."""
    form = financial_form(census=True, ministry=True)
    form["family"].update(lastName="Squyres", mailingName="Jeff Squyres")
    return form


def step_text(page):
    """The visible "Step N of M: Title" line."""
    return page.locator(".family-step").inner_text()


def next_page(page):
    """Select the navigation's Next button."""
    page.get_by_role("button", name="Next", exact=True).click()


def capture(page, name):
    """Save a screenshot when the run asks for them (never in CI)."""
    directory = os.environ.get("FAMILY_SCREENSHOTS")
    if directory:
        engine = page.context.browser.browser_type.name
        width = page.viewport_size["width"]
        path = Path(directory) / f"{engine}-{width}-{name}.png"
        page.screenshot(path=str(path), full_page=True)


@pytest.mark.parametrize("width", [390, 1280])
def test_pages_navigate_with_buttons_steps_and_browser_history(
    page, component_origin, axe_source, width
):
    """Each page is reachable forward and back; focus lands on its heading."""
    page.set_viewport_size({"width": width, "height": 900})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    begin(page, component_origin, paged_form(), None)
    expect(
        page.get_by_role("heading", name="The Squyres Family", level=2)
    ).to_be_visible()
    titles = page.locator("[data-step-link]").evaluate_all(
        "rows => rows.map(row => row.textContent)"
    )
    assert titles[0] == "Welcome" and titles[1] == "Family information"
    assert titles[-2:] == ["Financial stewardship", "Additional information"]
    total = len(titles) + 1
    page.evaluate(axe_source)
    for index, title in enumerate(titles):
        assert step_text(page) == f"Step {index + 1} of {total}: {title}"
        heading = page.get_by_role("heading", name=title, level=3)
        expect(heading).to_be_visible()
        expect(heading).to_be_focused()
        assert page.evaluate(
            "document.documentElement.scrollWidth <= window.innerWidth"
        )
        assert page.evaluate(AXE) == []
        capture(page, f"{index + 1:02d}-{title.lower().replace(' ', '-')}")
        if index == 1:
            # The information page names the household and labels the mailing
            # name that used to appear alone in an unlabelled pane.
            expect(
                page.get_by_text("Parish mail is addressed to: Jeff Squyres")
            ).to_be_visible()
        if title == "Financial stewardship":
            # The annual pledge is required; zero is a valid answer.
            page.get_by_label("Annual pledge (USD)").fill("0")
        if index < len(titles) - 1:
            next_page(page)
    expect(page.get_by_role("button", name="Review response")).to_be_visible()
    expect(page.get_by_role("button", name="Next", exact=True)).to_be_hidden()
    page.go_back()
    assert step_text(page).endswith(titles[-2])
    page.go_forward()
    assert step_text(page).endswith(titles[-1])
    page.get_by_role("button", name="Back", exact=True).click()
    assert step_text(page).endswith(titles[-2])
    page.locator("details.family-steps > summary").click()
    page.get_by_role("button", name="Welcome", exact=True).click()
    assert step_text(page) == f"Step 1 of {total}: Welcome"
    expect(page.get_by_role("button", name="Back", exact=True)).to_be_hidden()
    assert not errors


def test_next_checks_only_the_current_page(page, component_origin):
    """A missing required answer keeps the Family on that page with an error."""
    begin(page, component_origin, paged_form(), None)
    next_page(page)
    next_page(page)
    first = page.locator('input[id$="-first_name"]').first
    expect(first).to_be_visible()
    first.fill("")
    next_page(page)
    expect(first).to_be_focused()
    assert first.get_attribute("aria-invalid") == "true"
    assert "Family information" not in step_text(page)
    first.fill("Alex")
    next_page(page)
    expect(
        page.get_by_role("heading", name="Financial stewardship", level=3)
    ).to_be_visible()


@pytest.mark.parametrize("pledge", ["0", "0.00"])
def test_zero_pledge_hides_and_omits_frequency_and_sharing(
    page, component_origin, pledge
):
    """Nothing is required or submitted beyond the amount for a zero pledge."""
    submissions = []

    def submit(route):
        """Record the final aggregate."""
        submissions.append(route.request.post_data_json["answers"])
        route.fulfill(json={"accepted": True})

    begin(page, component_origin, paged_form(), submit)
    annual = show(page, page.get_by_label("Annual pledge (USD)"))
    annual.fill("250")
    page.get_by_label("Pledge frequency").select_option("monthly")
    page.locator(f"#financial-option-{CHECK}").check()
    annual = page.get_by_label("Annual pledge (USD)")
    annual.fill(pledge)
    expect(page.get_by_label("Pledge frequency")).to_be_hidden()
    expect(page.get_by_text("How would you like to share", exact=False)).to_be_hidden()
    expect(annual).to_be_focused()
    capture(page, "financial-zero")
    review(page)
    expect(page.get_by_text("No share methods selected.")).to_have_count(0)
    page.get_by_role("button", name="Submit to Sample Parish").click()
    expect(page.get_by_role("heading", name="Thank you!", exact=True)).to_be_visible()
    financial = submissions[0]["financial"]
    assert financial["frequency"] == "" and financial["shares"] == {}


def test_positive_pledge_requires_frequency_and_a_share_method(page, component_origin):
    """Both conditional fields appear, are required, and are re-cleared at zero."""
    submissions = []

    def submit(route):
        """Record the final aggregate."""
        submissions.append(route.request.post_data_json["answers"])
        route.fulfill(json={"accepted": True})

    begin(page, component_origin, paged_form(), submit)
    annual = show(page, page.get_by_label("Annual pledge (USD)"))
    annual.fill("1200")
    frequency = page.get_by_label("Pledge frequency (required)")
    expect(frequency).to_be_visible()
    frequency.select_option("")
    for box in page.locator('input[id^="financial-option-"]').all():
        box.uncheck()
    capture(page, "financial-positive")
    review(page)
    expect(page.get_by_text("Select how often you will give.")).to_be_visible()
    frequency = page.get_by_label("Pledge frequency (required)")
    frequency.select_option("monthly")
    review(page)
    expect(
        page.get_by_text("Choose at least one way to share your pledge.")
    ).to_be_visible()
    assert not submissions
    page.locator(f"#financial-option-{CHECK}").check()
    # Clearing the amount clears the hidden answers; they do not come back.
    page.get_by_label("Annual pledge (USD)").fill("")
    page.get_by_label("Annual pledge (USD)").fill("1200")
    expect(page.get_by_label("Pledge frequency (required)")).to_have_value("")
    expect(page.locator(f"#financial-option-{CHECK}")).not_to_be_checked()
    page.get_by_label("Pledge frequency (required)").select_option("annual")
    page.locator(f"#financial-option-{CHECK}").check()
    review(page)
    page.get_by_role("button", name="Submit to Sample Parish").click()
    expect(page.get_by_role("heading", name="Thank you!", exact=True)).to_be_visible()
    financial = submissions[0]["financial"]
    assert financial["frequency"] == "annual" and financial["shares"] == {CHECK: ""}


def test_ministry_rows_state_each_choice_once(page, component_origin):
    """Continuing is the default; stop and join choices read plainly on review."""
    submissions = []

    def submit(route):
        """Record the final aggregate."""
        submissions.append(route.request.post_data_json["answers"])
        route.fulfill(json={"accepted": True})

    begin(page, component_origin, paged_form(), submit)
    choir = show(page, page.get_by_role("group", name="Choir", include_hidden=True))
    expect(choir.get_by_label("Continuing")).to_be_checked()
    body = page.locator("main").inner_text()
    assert "These are requests" not in body and "wishes to stop" not in body
    choir.get_by_label("Stop participating").check()
    page.get_by_text("Click here to join another ministry", exact=True).click()
    page.get_by_role("checkbox", name="Food pantry", exact=True).check()
    expect(page.get_by_text("Joining: Food pantry", exact=True)).to_be_visible()
    capture(page, "member-ministries")
    show(page, page.get_by_label("Annual pledge (USD)")).fill("0")
    review(page)
    for text in ("Will continue: None", "Stopping: Choir", "Joining: Food pantry"):
        expect(page.get_by_text(text, exact=True)).to_be_visible()
    assert "may follow up" not in page.locator("main").inner_text()
    capture(page, "review")
    page.get_by_role("button", name="Submit to Sample Parish").click()
    expect(page.get_by_role("heading", name="Thank you!", exact=True)).to_be_visible()
    assert submissions[0]["ministries"]["members"] == {"3": {"join": [9], "leave": [4]}}


def test_new_member_page_title_follows_the_typed_name(page, component_origin):
    """A proposed person's page stops saying "Household member" as names are typed."""
    begin(page, component_origin, paged_form(), None)
    add = show(
        page,
        page.get_by_role("button", name="Add a household member", include_hidden=True),
    )
    add.click()
    first = page.locator('input[id$="-first_name"]:focus')
    expect(first).to_be_visible()
    first.fill("Jamie")
    key = first.evaluate("e => e.closest('[data-page]').dataset.page")
    expect(page.get_by_role("heading", name="Jamie", level=3)).to_be_visible()
    assert step_text(page).endswith(": Jamie")
    link = page.locator(f'[data-step-link="{key}"]')
    expect(link).to_have_text("Jamie")


def test_new_member_submits_every_field_including_blank_phones(page, component_origin):
    """A new person has no stored phone, so blank phones are sent as empty text."""
    from .test_member_requests import fill_new

    submissions = []

    def submit(route):
        """Record the final aggregate."""
        submissions.append(route.request.post_data_json["answers"])
        route.fulfill(json={"accepted": True})

    form = paged_form()
    begin(page, component_origin, form, submit)
    identifier = fill_new(page)
    show(page, page.get_by_label("Annual pledge (USD)")).fill("0")
    review(page)
    page.get_by_role("button", name="Submit to Sample Parish").click()
    expect(page.get_by_role("heading", name="Thank you!", exact=True)).to_be_visible()
    added = submissions[0]["proposed_members"][identifier]
    assert set(added) == {field["name"] for field in form["new_member_fields"]}
    for name in ("home_phone", "mobile_phone", "work_phone"):
        assert added[name] == ""


def test_enter_in_a_field_moves_to_the_next_page_not_review(page, component_origin):
    """A phone keyboard's Go must not skip every remaining page."""
    begin(page, component_origin, paged_form(), None)
    next_page(page)
    next_page(page)
    first = page.locator('input[id$="-first_name"]').first
    first.press("Enter")
    expect(
        page.get_by_role("heading", name="Financial stewardship", level=3)
    ).to_be_visible()
    assert page.get_by_role("button", name="Submit to Sample Parish").count() == 0
