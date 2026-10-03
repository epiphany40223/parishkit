"""The paged Family form: navigation, per-page checks and conditional fields."""

import io
import os
import re
from pathlib import Path

import pytest
from PIL import Image

from ..test_financial_answers import CHECK
from .test_family_financial import financial_form
from .test_family_ministry import begin, ministry_form
from .test_family_response import (
    expect,
    locked,
    review,
    show,
    unseen,
    visit_every_page,
)

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
            # The household is named by the heading on every page; ParishSoft's
            # one-person mailing name is not shown anywhere.
            expect(page.get_by_text("Parish mail is addressed to")).to_have_count(0)
            expect(page.get_by_text("Jeff Squyres")).to_have_count(0)
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
    page.locator('[data-step-link="intro"]').click()
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
    # Next checks each page it leaves, so go through every page with a valid
    # pledge first; the incomplete answers are then left for Review to catch.
    annual = show(page, page.get_by_label("Annual pledge (USD)"))
    annual.fill("0")
    visit_every_page(page)
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
    # A zero pledge clears the hidden answers; they do not come back.
    page.get_by_label("Annual pledge (USD)").fill("0")
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
    """Continuing in a ministry is the default; stop and join read plainly."""
    submissions = []

    def submit(route):
        """Record the final aggregate."""
        submissions.append(route.request.post_data_json["answers"])
        route.fulfill(json={"accepted": True})

    begin(page, component_origin, paged_form(), submit)
    choir = show(page, page.get_by_role("group", name="Choir", include_hidden=True))
    expect(choir.get_by_label("Continue in this ministry")).to_be_checked()
    body = page.locator("main").inner_text()
    assert "These are requests" not in body and "wishes to stop" not in body
    choir.get_by_label("Stop participating in this ministry").check()
    page.get_by_text("Click here to join more ministries", exact=True).click()
    page.get_by_role("checkbox", name="Food pantry", exact=True).check()
    expect(
        page.locator(".ministry-joining li", has_text="Food pantry").first
    ).to_be_visible()
    capture(page, "member-ministries")
    show(page, page.get_by_label("Annual pledge (USD)")).fill("0")
    review(page)
    expect(page.get_by_text("Continuing: None", exact=True)).to_be_visible()
    # Stopping and joining are bulleted, one ministry per line.
    expect(page.locator(".stopping li", has_text="Choir")).to_be_visible()
    expect(page.locator(".ministry-joining li", has_text="Food pantry")).to_be_visible()
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


LOCKED = ". Not available yet. Use Next to continue."


def test_step_bar_names_each_step_and_never_jumps_ahead(page, component_origin):
    """One segment per step plus Review; tooltips on focus; no jumping ahead (#330)."""
    page.set_viewport_size({"width": 390, "height": 900})
    begin(page, component_origin, paged_form(), None)
    segments = page.locator(".family-track button")
    # The step bar is built by script; wait for it before counting titles,
    # or a fast read sees none and expects a one-segment bar.
    expect(page.locator('[data-step-link="intro"]')).to_be_attached()
    titles = page.locator("[data-step-link]").evaluate_all(
        "rows => rows.map(row => row.textContent)"
    )
    total = len(titles) + 1
    expect(segments).to_have_count(total)
    expect(page.locator(".family-steps")).to_have_count(0)
    first = page.locator('[data-step-link="intro"]')
    expect(first).to_have_attribute("aria-current", "step")
    assert first.get_attribute("data-tip") == f"Step 1 of {total}: Welcome"
    assert not locked(first)
    # Every later step, and Review, is not available yet: announced as
    # unavailable, with a tooltip saying to use Next.
    review_segment = page.locator("[data-step-review]")
    assert review_segment.get_attribute("data-tip") == (
        f"Step {total} of {total}: Review and submit{LOCKED}"
    )
    second = page.locator('[data-step-link="household"]')
    third = page.locator("[data-step-link]").nth(2)
    for segment, name in (
        (second, "Family information"),
        (third, titles[2]),
        (review_segment, "Review and submit"),
    ):
        expect(segment).to_have_attribute("aria-disabled", "true")
        # The tooltip shown on focus stays out of the name, so the locked
        # text is announced once, as the description.
        segment.focus()
        expect(segment).to_have_accessible_name(name)
        expect(segment).to_have_accessible_description(
            "Not available yet. Use Next to continue."
        )
    # The tooltip text shows on keyboard focus, not only on hover.
    second.focus()
    assert second.evaluate("e => getComputedStyle(e, '::after').display") == "block"
    assert second.get_attribute("data-tip") == (
        f"Step 2 of {total}: Family information{LOCKED}"
    )
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    # Every segment's tooltip stays inside a phone-width viewport.
    for index in range(total):
        segments.nth(index).focus()
        assert page.evaluate(
            "document.documentElement.scrollWidth <= window.innerWidth"
        ), index
    # Neither a click nor the keyboard moves ahead. Playwright treats an
    # aria-disabled button as disabled, so the clicks are forced.
    second.click(force=True)
    second.focus()
    page.keyboard.press("Enter")
    page.keyboard.press(" ")
    review_segment.click(force=True)
    assert step_text(page) == f"Step 1 of {total}: Welcome"
    # A tap shows no tooltip on a phone, so activating a locked segment says
    # why beside the navigation buttons; the next page change clears it.
    note = page.locator("[data-nav-error]")
    expect(note).to_be_visible()
    expect(note).to_have_text("Not available yet. Use Next to continue.")
    # Next moves forward one page and opens that page's segment only.
    next_page(page)
    assert step_text(page) == f"Step 2 of {total}: Family information"
    expect(note).to_be_hidden()
    expect(second).to_have_attribute("aria-current", "step")
    assert not locked(second)
    assert second.get_attribute("data-tip") == f"Step 2 of {total}: Family information"
    expect(first).to_have_class("family-track-done family-tip-start")
    expect(third).to_have_attribute("aria-disabled", "true")
    # Going back keeps the furthest page reached available, to return to.
    first.click()
    assert step_text(page) == f"Step 1 of {total}: Welcome"
    assert not locked(second)
    expect(third).to_have_attribute("aria-disabled", "true")
    second.click()
    assert step_text(page) == f"Step 2 of {total}: Family information"
    # Once every page has been seen, every segment moves freely. Next checks
    # each page it leaves, so the required pledge is answered on the way and
    # cleared again afterwards for Review to catch.
    show(page, page.get_by_label("Annual pledge (USD)")).fill("0")
    visit_every_page(page)
    show(page, page.get_by_label("Annual pledge (USD)")).fill("")
    for index in range(total):
        assert not locked(segments.nth(index)), index
    first.click()
    page.locator("[data-step-link]").last.click()
    assert step_text(page) == f"Step {total - 1} of {total}: {titles[-1]}"
    first.click()
    # A missing required answer (the annual pledge) takes the Family to it;
    # its own error line explains it, so the note naming the question and
    # page is for screen readers only (#295).
    review_segment.click()
    assert step_text(page).endswith(": Financial stewardship")
    expect(page.get_by_label("Annual pledge (USD)")).to_be_focused()
    error = page.locator("#financial-annual-hint")
    expect(error).to_have_text("Enter an annual pledge.")
    assert unseen(note)
    expect(page.get_by_label("Annual pledge (USD)")).to_have_accessible_description(
        re.compile("“Annual pledge” on the “Financial stewardship” page")
    )
    expect(review_segment).not_to_have_attribute("aria-current", "step")


@pytest.mark.parametrize("returning", [False, True])
def test_review_needs_every_page_even_from_browser_history(
    page, component_origin, returning
):
    """No Family, first-time or returning, reaches Review with a page unseen (#330)."""
    form = paged_form()
    if returning:
        form["last_submitted_at"] = "2026-09-28T11:15:00+00:00"
        form["last_submitted_display"] = "September 28, 2026 at 7:15 AM EDT"
    begin(page, component_origin, form, None)
    show(page, page.get_by_label("Annual pledge (USD)")).fill("0")
    review_segment = page.locator("[data-step-review]")
    expect(review_segment).to_have_attribute("aria-disabled", "true")
    review_segment.click(force=True)
    assert step_text(page).endswith(": Financial stewardship")
    # A Review entry in browser history is checked the same way: the Family
    # is taken to the first page not yet seen, with a note saying why.
    page.evaluate(
        """() => {
          history.pushState({familyPage: "review"}, "");
          history.pushState({familyPage: "financial"}, "");
          history.back();
        }"""
    )
    note = page.locator("[data-nav-error]")
    expect(note).to_contain_text("Please go through each page")
    assert step_text(page).endswith(": Additional information")
    # The note also describes the focused heading, as the alert may be missed.
    heading = page.locator("h3:focus")
    expect(heading).to_have_attribute(
        "aria-describedby", re.compile("family-nav-error")
    )
    expect(page.locator(".family-step")).not_to_contain_text("Review and submit")
    # With every page seen, Review opens.
    review_segment.click()
    expect(page.locator(".family-step")).to_contain_text("Review and submit")


def test_welcome_page_has_no_duplicate_heading_or_session_deadline(
    page, component_origin
):
    """The welcome text brings its own heading; the session deadline is not shown."""
    form = paged_form()
    form["content"]["welcome"] = "<h2>Welcome to the renewal</h2><p>Hello.</p>"
    begin(page, component_origin, form, None)
    welcome = page.get_by_role("heading", name="Welcome", exact=True, level=3)
    expect(welcome).to_have_class("visually-hidden")
    expect(page.get_by_text("Session deadline")).to_have_count(0)
    expect(page.locator(".family-submitted")).to_have_count(0)


def test_returning_family_sees_when_they_last_submitted(page, component_origin):
    """The welcome page tells a returning Family their last submission time."""
    form = paged_form()
    form["last_submitted_at"] = "2026-09-28T11:15:00+00:00"
    form["last_submitted_display"] = "September 28, 2026 at 7:15 AM EDT"
    begin(page, component_origin, form, None)
    banner = page.locator(".family-submitted")
    expect(banner).to_have_text(
        "You last submitted your renewal on September 28, 2026 at 7:15 AM EDT. "
        "You can review, change and submit again as many times as you like; "
        "your most recent submission is the one we use."
    )
    # It is information only: navigation continues normally.
    next_page(page)
    assert "Family information" in step_text(page)


def test_review_page_step_bar_jumps_back_into_editing(page, component_origin):
    """From Review, a page segment reopens that page with the answers intact."""
    begin(page, component_origin, paged_form(), None)
    show(page, page.get_by_label("Annual pledge (USD)")).fill("0")
    review(page)
    review_segment = page.locator("[data-step-review]")
    expect(review_segment).to_have_attribute("aria-current", "step")
    expect(page.locator("[data-step-link]")).to_have_count(0)
    page.locator('[data-step-jump="financial"]').click()
    assert step_text(page).endswith(": Financial stewardship")
    expect(page.get_by_label("Annual pledge (USD)")).to_have_value("0")


def viewport_top(page):
    """How far the window is scrolled down, in CSS pixels."""
    return page.evaluate("window.scrollY")


@pytest.mark.parametrize("width", [390, 1280])
def test_sign_in_and_next_show_the_top_of_the_page(page, component_origin, width):
    """Page changes scroll to the top, then focus the heading without scrolling.

    Focusing a heading alone scrolls it to the top of the viewport and hides
    the campaign title, Family name and step bar above it (#220).
    """
    page.set_viewport_size({"width": width, "height": 500})
    begin(page, component_origin, paged_form(), None)
    assert viewport_top(page) == 0
    expect(page.get_by_role("heading", level=1)).to_be_in_viewport()
    assert "#" not in page.url
    for _ in range(3):
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        next_page(page)
        assert viewport_top(page) == 0
        focused = page.evaluate("document.activeElement.tagName")
        assert focused == "H3"
        expect(page.get_by_role("heading", level=1)).to_be_in_viewport()
    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    page.go_back()
    expect(page.locator("[data-page]:not([hidden]) h3")).to_be_focused()
    assert viewport_top(page) == 0


@pytest.mark.parametrize("width", [390, 1280])
def test_share_option_checkboxes_line_up_with_their_labels(
    page, component_origin, width
):
    """Each share checkbox sits beside the first line of its (wrapping) label."""
    page.set_viewport_size({"width": width, "height": 900})
    form = paged_form()
    long = (
        "We will have my bank send a check to the parish every month using "
        "our bank's online bill payment service"
    )
    form["financial"]["options"][0]["labels"] = {
        "none": long,
        "one": long,
        "many": long,
    }
    begin(page, component_origin, form, None)
    show(page, page.get_by_label("Annual pledge (USD)")).fill("100")
    boxes = page.locator('input[id^="financial-option-"]')
    expect(boxes.first).to_be_visible()
    offsets = boxes.evaluate_all(
        """inputs => inputs.map(input => {
            const label = input.closest('label');
            const text = [...label.childNodes]
                .find(n => n.nodeType === 3 && n.textContent.trim());
            const range = document.createRange();
            range.selectNodeContents(text);
            const line = range.getClientRects()[0];
            const box = input.getBoundingClientRect();
            return {
                gap: Math.abs(
                    (box.top + box.height / 2) - (line.top + line.height / 2)
                ),
                indent: [...range.getClientRects()].every(r => r.left >= box.right),
            };
        })"""
    )
    for offset in offsets:
        assert offset["gap"] <= 4, offsets
        assert offset["indent"], offsets


@pytest.mark.parametrize("width", [390, 1280])
def test_optional_closing_page_sits_between_financial_and_additional(
    page, component_origin, axe_source, width
):
    """Closing content adds one content-only step; without it there is none."""
    page.set_viewport_size({"width": width, "height": 900})
    form = paged_form()
    form["content"]["closing"] = (
        "<h2>Protect the earth</h2><p>Walk, carpool, or take public transit.</p>"
    )
    begin(page, component_origin, form, None)
    expect(page.locator('[data-step-link="intro"]')).to_be_attached()
    titles = page.locator("[data-step-link]").evaluate_all(
        "rows => rows.map(row => row.textContent)"
    )
    assert titles[-3:] == ["Financial stewardship", "Closing", "Additional information"]
    # Next checks each page it leaves; the annual pledge is required.
    show(page, page.get_by_label("Annual pledge (USD)")).fill("0")
    show(page, page.locator('[data-page="closing"]'))
    # The parish text's own heading is the visible title; "Closing" stays for
    # screen readers and focus only.
    expect(page.get_by_role("heading", name="Protect the earth")).to_be_visible()
    expect(page.locator("#page-closing-title")).to_be_focused()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.evaluate(axe_source)
    assert page.evaluate(AXE) == []
    next_page(page)
    expect(
        page.get_by_role("heading", name="Additional information", level=3)
    ).to_be_visible()


def test_no_closing_content_means_no_closing_step(page, component_origin):
    """Removing the closing content removes its step from the Family form."""
    begin(page, component_origin, paged_form(), None)
    expect(page.locator('[data-step-link="intro"]')).to_be_attached()
    expect(page.locator('[data-step-link="closing"]')).to_have_count(0)


def png(width, height):
    """PNG bytes for a routed campaign image."""
    stream = io.BytesIO()
    Image.new("RGB", (width, height), "green").save(stream, format="PNG")
    return stream.getvalue()


@pytest.mark.parametrize("width", [390, 1280])
def test_campaign_banner_and_page_icons(page, component_origin, axe_source, width):
    """Optional campaign images (#248) show without overflow and stay decorative."""
    page.set_viewport_size({"width": width, "height": 900})
    page.route(
        "**/branding/*.png",
        lambda route: route.fulfill(
            body=png(1024, 217) if "banner" in route.request.url else png(256, 256),
            content_type="image/png",
        ),
    )
    form = paged_form()
    form["images"] = {
        "banner": {"url": "/branding/banner.png", "width": 1024, "height": 217},
        "welcome": {"url": "/branding/welcome.png", "width": 256, "height": 256},
        "financial": {"url": "/branding/financial.png", "width": 256, "height": 256},
    }
    form["content"]["welcome"] = "<p>Welcome to the renewal.</p>"
    begin(page, component_origin, form, None)
    intro = page.locator('[data-page="intro"]')
    # The wide banner is for emails only; Welcome shows just its icon, first
    # (no returning-Family notice here) and above the intro text.
    expect(page.locator("img.family-banner")).to_have_count(0)
    icon = intro.locator("img.family-page-icon")
    expect(icon).to_be_visible()
    assert icon.get_attribute("alt") == ""
    assert icon.evaluate(
        "i => Boolean(i.compareDocumentPosition(i.parentElement"
        ".querySelector('.content-block')) & Node.DOCUMENT_POSITION_FOLLOWING)"
    )
    assert icon.evaluate("e => e.getBoundingClientRect().width") <= 100
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.evaluate(axe_source)
    assert page.evaluate(AXE) == []
    # Pages without an image slot set show none.
    next_page(page)
    expect(page.locator('[data-page="household"] img')).to_have_count(0)
    show(page, page.locator('[data-page="financial"]'))
    expect(page.locator('[data-page="financial"] img.family-page-icon')).to_be_visible()


def test_no_campaign_images_means_no_images(page, component_origin):
    """A form without images renders no image elements on its pages."""
    begin(page, component_origin, paged_form(), None)
    expect(page.locator('[data-step-link="intro"]')).to_be_attached()
    expect(page.locator(".family-page img")).to_have_count(0)


@pytest.mark.parametrize("width", [390, 1280])
def test_blocked_next_says_which_question_needs_an_answer(
    page, component_origin, width
):
    """Next on the financial page without a share method explains itself once."""
    page.set_viewport_size({"width": width, "height": 900})
    form = paged_form()
    form["additional_enabled"] = True
    begin(page, component_origin, form, None)
    show(page, page.get_by_label("Annual pledge (USD)")).fill("1200")
    page.get_by_label("Pledge frequency (required)").select_option("monthly")
    before = step_text(page)
    next_page(page)
    assert step_text(page) == before
    # The group's own error line explains it; no second note (#295).
    note = page.locator("[data-nav-error]")
    expect(page.locator("#financial-shares-error")).to_be_visible()
    expect(note).to_be_hidden()
    focused = page.locator(":focus")
    expect(focused).to_have_attribute("aria-describedby", "financial-shares-error")
    # Focus brings the group into view above the sticky navigation.
    box, nav = focused.bounding_box(), page.locator(".family-nav").bounding_box()
    assert box and nav and box["y"] >= 0 and box["y"] + box["height"] <= nav["y"]
    # Only the share group is outlined in red, not the whole pledge section.
    for selector in (".financial-pledge", "#financial-section"):
        width = page.locator(selector).evaluate(
            "e => getComputedStyle(e).borderTopWidth"
        )
        assert width in ("0px", "1px"), (selector, width)
    group = page.locator("fieldset.choice-group")
    assert group.evaluate("e => getComputedStyle(e).borderTopWidth") == "2px"
    # Fixing the answer moves on.
    page.locator('#financial-section input[id^="financial-option-"]').first.check()
    next_page(page)
    expect(note).to_be_hidden()
    assert step_text(page) != before
    assert page.locator('[aria-describedby~="family-nav-error"]').count() == 0


def test_member_section_is_not_outlined_by_an_invalid_field(page, component_origin):
    """A Member's own invalid field doesn't draw a red box around the section."""
    begin(page, component_origin, paged_form(), None)
    first = show(page, page.get_by_label("First name (required)").first)
    first.fill("")
    next_page(page)
    expect(page.locator("[data-nav-error]")).to_be_hidden()
    section = page.locator("fieldset.member-section:visible").first
    assert section.evaluate("e => getComputedStyle(e).borderTopWidth") == "0px"


def test_blocked_review_opens_a_question_on_another_page(page, component_origin):
    """An added Member's missing name stops Review and opens that page."""
    begin(page, component_origin, paged_form(), None)
    show(page, page.get_by_label("Annual pledge (USD)")).fill("0")
    add = show(
        page,
        page.get_by_role("button", name="Add a household member", include_hidden=True),
    )
    add.click()
    name = page.locator("#" + page.locator(":focus").get_attribute("id"))
    key = name.evaluate("e => e.closest('[data-page]').dataset.page")
    # Next checks the page being left, so the new person needs a name to be
    # passed; clear it again once every page has been seen.
    name.fill("Jamie")
    visit_every_page(page)
    page.locator(f'[data-step-link="{key}"]').click()
    name.fill("")
    page.locator('[data-step-link="intro"]').click()
    page.locator("[data-step-review]").click()
    expect(page.locator(f'[data-page="{key}"]')).to_be_visible()
    # The focused name shows its own error line, so sighted Families see no
    # second note; a screen reader still hears which page Review opened.
    focused = page.locator(":focus")
    expect(focused).to_have_id(re.compile(r"-first_name$"))
    expect(page.locator(f"#{focused.get_attribute('id')}-inline-error")).to_be_visible()
    assert unseen(page.locator("[data-nav-error]"))
    title = page.locator(f'[data-page="{key}"] h3').inner_text()
    expect(focused).to_have_accessible_description(
        re.compile(f"“First name” on the “{re.escape(title)}” page")
    )


FIRST_CONTROL = """page => {
  const control = [...page.querySelectorAll('input, select, textarea, button, summary')]
    .find(e => e.getClientRects().length && !e.closest('.visually-hidden'));
  const nav = document.querySelector('.family-nav').getBoundingClientRect();
  return {top: control.getBoundingClientRect().top, visible: nav.top, id: control.id};
}"""


@pytest.mark.parametrize("census", [True, False])
def test_member_page_first_control_fits_a_phone(page, component_origin, census):
    """#292: at 390x844 the first Member control shows without scrolling."""
    from parishkit.stewardship.accounts.content_defaults import PAGES

    page.set_viewport_size({"width": 390, "height": 844})
    form = paged_form() if census else ministry_form(census=False)
    form["content"]["member_census"] = PAGES["member_census"]
    form["content"]["ministry"] = PAGES["ministry"]
    begin(page, component_origin, form, None)
    key = page.locator("[data-step-link^=member-]").first.get_attribute(
        "data-step-link"
    )
    show(page, page.locator(f'[data-page="{key}"]'))
    found = page.locator(f'[data-page="{key}"]').evaluate(FIRST_CONTROL)
    # Visible without scrolling: above the sticky navigation, and so also
    # within the 844 px viewport.
    assert found["top"] < found["visible"] <= 844, found


def test_ministry_choice_labels_wrap_on_a_phone(page, component_origin):
    """The longer choice labels stay inside their row at 320 and 390 px."""
    page.set_viewport_size({"width": 320, "height": 844})
    begin(page, component_origin, ministry_form(census=False), None)
    choir = page.get_by_role("group", name="Choir", include_hidden=True)
    stop = show(page, choir.get_by_label("Stop participating in this ministry"))
    expect(stop).to_be_visible()
    row = choir.bounding_box()
    for label in choir.locator("label").all():
        box = label.bounding_box()
        assert (
            box["x"] >= row["x"] and box["x"] + box["width"] <= row["x"] + row["width"]
        )
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def test_review_lists_continuing_ministries_one_per_line(page, component_origin):
    """Continuing ministries are bulleted on Review, like Stopping and Joining."""
    form = ministry_form(census=False)
    form["ministries"]["options"].append({"id": 11, "name": "Greeters"})
    form["ministries"]["members"]["3"]["current"] = [4, 11]
    begin(page, component_origin, form, None)
    review(page)
    expect(page.locator(".ministry-continuing > p")).to_have_text("Continuing:")
    expect(page.locator(".ministry-continuing li")).to_have_text(["Choir", "Greeters"])


WELCOME_ORDER = """() => {
  const tag = e => e.matches('p.notice[role=status]')
      && e.textContent.startsWith('Testing mode') ? 'banner'
    : e.matches('.family-submitted') ? 'notice'
    : e.matches('#family-flow > h2') ? 'family'
    : e.matches('[data-page=intro] img.family-page-icon') ? 'icon'
    : e.matches('[data-page=intro] h3:not(.visually-hidden)') ? 'welcome' : null;
  return [...document.querySelectorAll('main *')].filter(e => e.getClientRects().length)
    .map(tag).filter(Boolean);
}"""


@pytest.mark.parametrize("testing", [True, False])
@pytest.mark.parametrize("submitted", [True, False])
def test_welcome_order_banner_notice_family_icon(
    page, component_origin, testing, submitted
):
    """#292: Testing banner, last-submitted notice, Family heading, then icon."""
    from .test_family_response import prepare

    page.route(
        "**/branding/*.png",
        lambda route: route.fulfill(body=png(256, 256), content_type="image/png"),
    )
    form = paged_form()
    form["testing"] = testing
    form["content"]["welcome"] = ""
    form["images"] = {
        "welcome": {"url": "/branding/welcome.png", "width": 256, "height": 256},
    }
    if submitted:
        form["last_submitted_at"] = "2026-09-28T11:15:00+00:00"
        form["last_submitted_display"] = "September 28, 2026 at 7:15 AM EDT"
    prepare(page, component_origin, testing=testing, form=form)
    expect(page.locator('[data-page="intro"]')).to_be_visible()
    expected = (
        (["banner"] if testing else [])
        + (["notice"] if submitted else [])
        + ["family", "icon", "welcome"]
    )
    assert page.evaluate(WELCOME_ORDER) == expected
    # The focused Welcome heading is described by the notice, so screen
    # readers hear it even though it sits above the focus.
    welcome_heading = page.locator('[data-page="intro"] h3')
    if submitted:
        expect(welcome_heading).to_have_attribute(
            "aria-describedby", "family-last-submitted"
        )
        expect(welcome_heading).to_be_focused()
        expect(welcome_heading).to_have_accessible_description(
            re.compile("You last submitted your renewal")
        )
    else:
        assert welcome_heading.get_attribute("aria-describedby") is None
    # The notice belongs to the Welcome page only.
    next_page(page)
    expect(page.locator(".family-submitted")).to_be_hidden()
    page.locator('[data-step-link="intro"]').click()
    if submitted:
        expect(page.locator(".family-submitted")).to_be_visible()


def test_parish_text_page_links_open_in_a_new_tab(page, component_origin):
    """Following a link in parish text never discards the tab's answers (#300)."""
    form = paged_form()
    form["content"]["welcome"] = (
        '<p><a href="https://example.org/give">Give online</a>, '
        '<a href="/files/guide">the guide</a>, '
        '<a href="mailto:office@example.org">office@example.org</a>, '
        '<a href="MAILTO:desk@example.org">desk@example.org</a>, '
        '<a href="Tel:+15555550100">call</a> or '
        '<a href="/family/">return to your form</a>.</p>'
    )
    begin(page, component_origin, form, None)
    block = page.locator('[data-page="intro"] .content-block')
    for name in ("Give online", "the guide"):
        link = block.get_by_role("link", name=name)
        expect(link).to_have_attribute("target", "_blank")
        assert set(link.get_attribute("rel").split()) >= {"noopener", "noreferrer"}
    # Email and phone links (any case) and a link back to this form stay.
    for name in (
        "office@example.org",
        "desk@example.org",
        "call",
        "return to your form",
    ):
        assert block.get_by_role("link", name=name).get_attribute("target") is None
