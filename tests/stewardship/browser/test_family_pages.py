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


def test_step_bar_names_each_step_and_jumps(page, component_origin):
    """One segment per step plus Review; tooltips on focus; tap or click jumps."""
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
    review_segment = page.locator("[data-step-review]")
    assert review_segment.get_attribute("data-tip") == (
        f"Step {total} of {total}: Review and submit"
    )
    # The tooltip text shows on keyboard focus, not only on hover.
    second = page.locator('[data-step-link="household"]')
    second.focus()
    assert second.evaluate("e => getComputedStyle(e, '::after').display") == "block"
    assert second.get_attribute("data-tip") == f"Step 2 of {total}: Family information"
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    # Every segment's tooltip stays inside a phone-width viewport.
    for index in range(total):
        segments.nth(index).focus()
        assert page.evaluate(
            "document.documentElement.scrollWidth <= window.innerWidth"
        ), index
    second.click()
    assert step_text(page) == f"Step 2 of {total}: Family information"
    expect(second).to_have_attribute("aria-current", "step")
    expect(first).to_have_class("family-track-done family-tip-start")
    # Review first requires every page to have been viewed: the Family is
    # taken to the first page not yet seen, with a note saying why.
    note = page.locator("[data-nav-error]")
    review_segment.click()
    assert step_text(page) == f"Step 3 of {total}: Alex Sample"
    expect(note).to_contain_text("Please go through each page")
    # The note also describes the focused heading, as the alert may be missed.
    heading = page.locator("h3:focus")
    expect(heading).to_have_attribute(
        "aria-describedby", re.compile("family-nav-error")
    )
    expect(review_segment).not_to_have_attribute("aria-current", "step")
    links = page.locator("[data-step-link]")
    for index in range(links.count()):
        links.nth(index).click()
    # With every page seen, a missing required answer (the annual pledge)
    # takes the Family to it, and the note names the question.
    review_segment.click()
    assert step_text(page).endswith(": Financial stewardship")
    expect(page.get_by_label("Annual pledge (USD)")).to_be_focused()
    expect(note).to_contain_text("“Annual pledge” on the “Financial stewardship” page")
    expect(note).to_be_visible()
    expect(review_segment).not_to_have_attribute("aria-current", "step")


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
    page.locator('[data-step-link="closing"]').click()
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
    page.locator('[data-step-link="household"]').click()
    expect(page.locator('[data-page="household"] img')).to_have_count(0)
    page.locator('[data-step-link="financial"]').click()
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
    """Next on the financial page without a share method explains itself."""
    page.set_viewport_size({"width": width, "height": 900})
    form = paged_form()
    form["additional_enabled"] = True
    begin(page, component_origin, form, None)
    show(page, page.get_by_label("Annual pledge (USD)")).fill("1200")
    page.get_by_label("Pledge frequency (required)").select_option("monthly")
    before = step_text(page)
    next_page(page)
    assert step_text(page) == before
    note = page.locator("[data-nav-error]")
    expect(note).to_be_visible()
    expect(note).to_have_text("Please check “How would you like to share?”.")
    expect(page.locator("#financial-shares-error")).to_be_visible()
    focused = page.locator(":focus")
    expect(focused).to_have_attribute(
        "aria-describedby", re.compile("family-nav-error")
    )
    # Only the share group is outlined in red, not the whole pledge section.
    for selector in (".financial-pledge", "#financial-section"):
        width = page.locator(selector).evaluate(
            "e => getComputedStyle(e).borderTopWidth"
        )
        assert width in ("0px", "1px"), (selector, width)
    group = page.locator("fieldset.choice-group")
    assert group.evaluate("e => getComputedStyle(e).borderTopWidth") == "2px"
    # The note sits with the (sticky) navigation, inside the viewport.
    box = note.bounding_box()
    assert box and box["y"] + box["height"] <= page.viewport_size["height"]
    # Fixing the answer clears the note on the next attempt.
    page.locator('#financial-section input[id^="financial-option-"]').first.check()
    next_page(page)
    expect(note).to_be_hidden()
    assert step_text(page) != before
    assert page.locator('[aria-describedby~="family-nav-error"]').count() == 0


def test_returning_family_may_go_straight_to_review(page, component_origin):
    """Someone who already submitted doesn't have to revisit every page."""
    form = paged_form()
    form["last_submitted_at"] = "2026-09-28T11:15:00+00:00"
    form["last_submitted_display"] = "September 28, 2026 at 7:15 AM EDT"
    begin(page, component_origin, form, None)
    show(page, page.get_by_label("Annual pledge (USD)")).fill("0")
    page.locator("[data-step-review]").click()
    expect(page.locator(".family-step")).to_contain_text("Review and submit")


def test_member_section_is_not_outlined_by_an_invalid_field(page, component_origin):
    """A Member's own invalid field doesn't draw a red box around the section."""
    begin(page, component_origin, paged_form(), None)
    first = show(page, page.get_by_label("First name (required)").first)
    first.fill("")
    next_page(page)
    expect(page.locator("[data-nav-error]")).to_contain_text("First name")
    section = page.locator("fieldset.member-section:visible").first
    assert section.evaluate("e => getComputedStyle(e).borderTopWidth") == "0px"


def test_blocked_review_names_a_question_on_another_page(page, component_origin):
    """An added Member's missing name stops Review and names that page."""
    begin(page, component_origin, paged_form(), None)
    show(page, page.get_by_label("Annual pledge (USD)")).fill("0")
    add = show(
        page,
        page.get_by_role("button", name="Add a household member", include_hidden=True),
    )
    add.click()
    key = page.locator(":focus").evaluate("e => e.closest('[data-page]').dataset.page")
    # Every page has been seen; go to another page and ask for Review.
    links = page.locator("[data-step-link]")
    for index in range(links.count()):
        links.nth(index).click()
    page.locator('[data-step-link="intro"]').click()
    page.locator("[data-step-review]").click()
    expect(page.locator(f'[data-page="{key}"]')).to_be_visible()
    note = page.locator("[data-nav-error]")
    expect(note).to_contain_text("First name")
    expect(note).to_contain_text("page")
    expect(page.locator(":focus")).to_have_attribute(
        "aria-describedby", re.compile("family-nav-error")
    )


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
    if not testing:
        page.get_by_role("button", name="Begin reviewing").click()
    expect(page.locator('[data-page="intro"]')).to_be_visible()
    expected = (
        (["banner"] if testing else [])
        + (["notice"] if submitted else [])
        + ["family", "icon", "welcome"]
    )
    assert page.evaluate(WELCOME_ORDER) == expected
    # The notice belongs to the Welcome page only.
    page.locator('[data-step-link="household"]').click()
    expect(page.locator(".family-submitted")).to_be_hidden()
    page.locator('[data-step-link="intro"]').click()
    if submitted:
        expect(page.locator(".family-submitted")).to_be_visible()
