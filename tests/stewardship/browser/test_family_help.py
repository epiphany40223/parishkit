"""Action-first Family pages: sticky actions, toggletips and full intro text (#225)."""

import pytest

from .test_family_ministry import begin
from .test_family_pages import next_page, paged_form
from .test_family_response import expect, review, show
from .waits import same_place

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

LONG_WELCOME = (
    "<h2>Welcome to the renewal</h2>"
    "<p>Thank you for taking a few minutes to renew your commitment.</p>"
    + "".join(
        f"<p>Paragraph {n} explains the campaign in more detail than most "
        "Families need before they start answering questions.</p>"
        for n in range(1, 5)
    )
)

# An element's box in page coordinates, so that scrolling does not count as
# the element moving.
PAGE_BOX = """e => { const r = e.getBoundingClientRect();
    return [r.x + scrollX, r.y + scrollY, r.width, r.height]; }"""


def phone_tip(page):
    """The first phone field's toggletip, with its page opened."""
    tip = page.locator(".label-row:has(+ input[id$='_phone']) .toggletip").first
    show(page, tip)
    return tip


def in_viewport(page, locator):
    """Whether the element's box lies fully inside the window."""
    return locator.evaluate(
        """e => { const r = e.getBoundingClientRect();
        return r.top >= 0 && r.left >= 0 && r.bottom <= innerHeight
          && r.right <= document.documentElement.clientWidth; }"""
    )


def test_phone_sticky_bar_stays_in_reach(page, component_origin):
    """On a phone the Back/Next bar stays on screen however long the page is."""
    page.set_viewport_size({"width": 390, "height": 640})
    begin(page, component_origin, paged_form(), None)
    next_page(page)
    next_page(page)
    page.evaluate("window.scrollTo(0, 400)")
    bar = page.locator(".family-nav")
    expect(page.get_by_role("button", name="Next", exact=True)).to_be_visible()
    assert in_viewport(page, bar)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


def test_review_submit_sits_in_the_sticky_bar(page, component_origin):
    """The Review page's Submit button is in the same sticky bar."""
    page.set_viewport_size({"width": 390, "height": 640})
    begin(page, component_origin, paged_form(), None)
    show(page, page.get_by_label("Annual pledge (USD)")).fill("0")
    review(page)
    bar = page.locator(".family-nav")
    expect(bar.get_by_role("button", name="Back to edit")).to_be_visible()
    expect(bar.get_by_role("button", name="Submit to Sample Parish")).to_be_visible()
    page.evaluate("window.scrollTo(0, 0)")
    assert in_viewport(page, bar)


@pytest.mark.parametrize("width", [390, 1280])
def test_toggletip_opens_by_click_and_keyboard_and_closes(
    page, component_origin, width
):
    """Click, tap, Enter and Space open it; Escape and outside clicks close it.

    The bubble floats over the page, so its phone field never moves (#1289).
    """
    page.set_viewport_size({"width": width, "height": 800})
    begin(page, component_origin, paged_form(), None)
    tip = phone_tip(page)
    button, bubble = tip.locator("button"), tip.locator(".toggletip-bubble")
    assert button.get_attribute("aria-label") == "More information"
    label_id = button.get_attribute("aria-describedby")
    assert page.locator(f"#{label_id}").inner_text().endswith("(optional)")
    expect(bubble).to_be_hidden()
    expect(bubble).to_contain_text("country code")
    field = page.locator(".label-row + input[id$='_phone']").first
    before = field.evaluate(PAGE_BOX)
    button.click()
    expect(button).to_have_attribute("aria-expanded", "true")
    expect(bubble).to_be_visible()
    assert same_place(field.evaluate(PAGE_BOX), before)
    assert in_viewport(page, bubble)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    button.click()
    expect(button).to_have_attribute("aria-expanded", "false")
    expect(bubble).to_be_hidden()
    button.focus()
    page.keyboard.press("Enter")
    expect(bubble).to_be_visible()
    page.keyboard.press("Escape")
    expect(bubble).to_be_hidden()
    expect(button).to_have_attribute("aria-expanded", "false")
    expect(button).to_be_focused()
    page.keyboard.press(" ")
    expect(bubble).to_be_visible()
    page.locator("h2").first.click()
    expect(bubble).to_be_hidden()


def test_only_one_toggletip_is_open_at_a_time(page, component_origin):
    """Opening a second bubble closes the first."""
    begin(page, component_origin, paged_form(), None)
    tip = phone_tip(page)
    tips = tip.locator("xpath=ancestor::section[@data-page]").locator(".toggletip")
    assert tips.count() >= 2
    tips.nth(0).locator("button").click()
    # The open bubble floats over the page and may cover the next field's
    # "i" (#1289), so reach that one from the keyboard.
    tips.nth(1).locator("button").focus()
    page.keyboard.press("Enter")
    expect(tips.nth(0).locator(".toggletip-bubble")).to_be_hidden()
    expect(tips.nth(1).locator(".toggletip-bubble")).to_be_visible()


@pytest.mark.parametrize("width", [390, 1280])
def test_long_intro_is_shown_in_full(page, component_origin, width):
    """Parish-written text always shows in full, with no "Read more" button."""
    page.set_viewport_size({"width": width, "height": 800})
    form = paged_form()
    form["content"]["welcome"] = LONG_WELCOME
    begin(page, component_origin, form, None)
    expect(page.get_by_text("Thank you for taking a few minutes")).to_be_visible()
    expect(page.get_by_text("Paragraph 4 explains")).to_be_visible()
    expect(page.get_by_role("button", name="Read more")).to_have_count(0)
