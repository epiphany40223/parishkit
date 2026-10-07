"""Typing and ticking on a phone keep the Financial page still (#295).

On iOS Safari each digit typed into Annual pledge, and each "how to give"
checkbox, made the whole screen flicker and jump. These tests drive a
phone-sized touch browser and prove the page is edited in place: the same
DOM nodes stay, the window does not scroll, and focus and caret stay put.
"""

from copy import deepcopy
from itertools import chain

import pytest

from ..test_financial_answers import CHECK, OTHER
from .test_family_financial import final_submit, financial_form
from .test_family_ministry import begin
from .test_family_response import expect, show

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "webkit"], indirect=True
)

# The controls whose identity must survive every keystroke and tick.
CONTROLS = [
    "financial-annual_pledge",
    "financial-frequency",
    f"financial-option-{CHECK}",
    f"financial-option-{OTHER}",
]

# Remember each control once; later calls report whether each is still the
# very same node in the document (a rebuild would replace it).
SAME_NODES = """ids => {
  window.keptNodes ||= Object.fromEntries(
    ids.map(id => [id, document.getElementById(id)]));
  return ids.filter(id => window.keptNodes[id] !== document.getElementById(id));
}"""


@pytest.fixture
def phone(browser_engine):
    """A 390x844 touch screen, like the iPhone the flicker was seen on."""
    context = browser_engine.new_context(
        viewport={"width": 390, "height": 844},
        has_touch=True,
        timezone_id="America/Los_Angeles",
        reduced_motion="reduce",
    )
    yield context.new_page()
    context.close()


def centered(page, locator):
    """Scroll ``locator`` to mid-screen, as a Family would, and return scrollY."""
    locator.evaluate("e => e.scrollIntoView({block: 'center'})")
    return page.evaluate("window.scrollY")


def test_typing_and_ticking_keep_the_page_still(phone, component_origin):
    """Digits and share ticks re-render nothing and never move the window."""
    page = phone
    form = financial_form()
    # Long Admin text, so the pledge field sits well down a scrolled page.
    form["content"]["financial"] = "<p>Thank you for your generosity.</p>" * 30
    begin(page, component_origin, form, None)
    annual = show(page, page.get_by_label("Annual pledge (USD)"))
    expect(annual).to_be_visible()
    top = centered(page, annual)
    assert top > 0
    annual.tap()
    expect(annual).to_be_focused()
    top = page.evaluate("window.scrollY")
    assert page.evaluate(SAME_NODES, CONTROLS) == []
    typed = ""
    for digit in "1200":
        page.keyboard.type(digit)
        typed += digit
        expect(annual).to_have_value(typed)
        assert page.evaluate(SAME_NODES, CONTROLS) == [], typed
        assert page.evaluate("window.scrollY") == top, typed
        expect(annual).to_be_focused()
        assert annual.evaluate("e => [e.selectionStart, e.selectionEnd]") == [
            len(typed),
            len(typed),
        ]
    # The first digit revealed frequency and share methods in place.
    expect(page.get_by_label("Pledge frequency (required)")).to_be_visible()
    for option in (CHECK, OTHER):
        box = page.locator(f"#financial-option-{option}")
        top = centered(page, box)
        box.tap()
        expect(box).to_be_checked()
        assert page.evaluate(SAME_NODES, CONTROLS) == [], option
        assert page.evaluate("window.scrollY") == top, option
        # Focus is not moved elsewhere (for example into the new details box,
        # which would raise the keyboard and scroll the page). WebKit, like
        # Safari, focuses the nearest focusable ancestor of a clicked checkbox.
        assert box.evaluate("e => document.activeElement.contains(e)"), option
    # The free-text method's details box appeared beside it, and unticking
    # hides it again, still without moving anything.
    details = page.get_by_label("Details for I will share another way")
    expect(details).to_be_visible()
    box = page.locator(f"#financial-option-{OTHER}")
    top = centered(page, box)
    box.tap()
    expect(details).to_be_hidden()
    assert page.evaluate(SAME_NODES, CONTROLS) == []
    # The box is last on the page, so the window may only settle at the new,
    # shorter page end; nothing scrolls it anywhere else.
    assert page.evaluate("window.scrollY") in (
        top,
        page.evaluate("document.documentElement.scrollHeight - innerHeight"),
    )
    expect(annual).to_have_value("1200")


def pledged(page, origin):
    """Open the form and enter a full pledge; return the Annual pledge field."""
    begin(page, origin, financial_form(), None)
    annual = show(page, page.get_by_label("Annual pledge (USD)"))
    annual.fill("1200")
    page.get_by_label("Pledge frequency (required)").select_option("monthly")
    page.locator(f"#financial-option-{CHECK}").check()
    page.locator(f"#financial-option-{OTHER}").check()
    page.get_by_label("Details for I will share another way").fill("Stock gift")
    assert page.evaluate(SAME_NODES, CONTROLS) == []
    return annual


def retype(page, annual, text):
    """Select the whole amount, type ``text`` one key at a time ("\b" is
    Backspace) and yield the value after each key."""
    annual.focus()
    annual.evaluate("e => e.select()")
    for key in text:
        if key == "\b":
            page.keyboard.press("Backspace")
        else:
            page.keyboard.type(key)
        yield annual.input_value()


def test_a_half_typed_amount_keeps_the_details(phone, component_origin):
    """#295: "1," on the way to "1,200" (or a cleared field) clears nothing."""
    page = phone
    annual = pledged(page, component_origin)
    details = page.get_by_label("Details for I will share another way")
    for typed in chain(retype(page, annual, "1,200"), retype(page, annual, "x\b")):
        # Not yet a number ("1,", "x", ""): the section and answers stay.
        expect(page.get_by_label("Pledge frequency (required)")).to_be_visible()
        expect(page.get_by_label("Pledge frequency (required)")).to_have_value(
            "monthly"
        )
        expect(page.locator(f"#financial-option-{CHECK}")).to_be_checked()
        expect(page.locator(f"#financial-option-{OTHER}")).to_be_checked()
        expect(details).to_have_value("Stock gift")
        assert page.evaluate(SAME_NODES, CONTROLS) == [], typed


def test_zero_and_back_clears_the_details_in_place(phone, component_origin):
    """An exact zero clears the details; a new amount doesn't bring them back."""
    page = phone
    annual = pledged(page, component_origin)
    list(retype(page, annual, "0"))
    expect(page.get_by_label("Pledge frequency (required)")).to_be_hidden()
    list(retype(page, annual, "250"))
    expect(page.get_by_label("Pledge frequency (required)")).to_have_value("")
    expect(page.locator(f"#financial-option-{CHECK}")).not_to_be_checked()
    expect(page.locator(f"#financial-option-{OTHER}")).not_to_be_checked()
    details = page.get_by_label("Details for I will share another way")
    expect(details).to_be_hidden()
    expect(details).to_have_value("")
    assert page.evaluate(SAME_NODES, CONTROLS) == []
    expect(annual).to_be_focused()


def test_zero_with_an_open_changed_record_choice_rebuilds(phone, component_origin):
    """The rare rebuild: a zero pledge must also drop a frequency conflict."""
    page = phone
    form = financial_form()
    form["financial"]["answers"] = {
        "annual_pledge": "12.00",
        "frequency": "annual",
        "shares": {},
        "cannot_give": False,
    }
    fresh = deepcopy(form)
    fresh["financial"]["answers"]["frequency"] = "monthly"
    page.route(
        "**/family/submit",
        lambda route: route.fulfill(
            status=409, json={"error": "review_required", "form": fresh}
        ),
    )
    begin(page, component_origin, form, None)
    show(page, page.get_by_label("Pledge frequency (required)")).select_option("weekly")
    page.locator(f"#financial-option-{CHECK}").check()
    final_submit(page)
    conflict = page.locator('[data-conflict="financial.frequency"]')
    expect(show(page, conflict)).to_be_visible()
    annual = page.get_by_label("Annual pledge (USD)")
    assert page.evaluate(SAME_NODES, CONTROLS) == []
    annual.fill("0")
    # Rebuilt: the choice is gone, the field is new and still has focus.
    expect(conflict).to_have_count(0)
    expect(page.get_by_label("Pledge frequency (required)")).to_be_hidden()
    assert "financial-annual_pledge" in page.evaluate(SAME_NODES, CONTROLS)
    expect(annual).to_be_focused()
    expect(annual).to_have_value("0")


# #384 L5: controls that still rebuild the form keep the page still around
# themselves: the control stays where it was on screen and keeps focus.
STILL = 2  # pixels a rebuilt page may settle by (subpixel layout rounding)


def screen_top(locator):
    """Where ``locator`` sits on screen, in CSS pixels from the viewport top."""
    return locator.evaluate("e => e.getBoundingClientRect().top")


def stays_put(page, locator, action, focused=None):
    """Scroll ``locator`` mid-screen, ``action()``, and check nothing moved."""
    centered(page, locator)
    before = screen_top(locator)
    assert page.evaluate("window.scrollY") > 0
    action()
    expect(focused or locator).to_be_focused()
    after = screen_top(locator)
    assert abs(after - before) <= STILL, (before, after)


def service_page(page, component_origin, *, last=False):
    """The talents form, with long Ministry text so its controls sit low.

    ``last`` drops the pages after the Member's, so it is the last page.
    """
    from .test_family_service import service_form

    form = service_form()
    form["content"]["ministry"] = "<p>Ministries serve our parish.</p>" * 30
    if last:
        form["additional_enabled"] = False
        form["content"].pop("closing", None)
    begin(page, component_origin, form, None)


def test_cannot_participate_keeps_the_page_still(phone, component_origin):
    """Ticking and unticking "cannot participate" does not jump the page."""
    from .test_family_service import SERVE

    page = phone
    service_page(page, component_origin)
    lock = show(page, page.get_by_label(SERVE))
    stays_put(page, page.get_by_label(SERVE), lambda: lock.check())
    expect(page.get_by_label(SERVE)).to_be_checked()
    stays_put(
        page, page.get_by_label(SERVE), lambda: page.get_by_label(SERVE).uncheck()
    )
    expect(page.get_by_label(SERVE)).not_to_be_checked()


def test_free_text_talent_keeps_the_page_still(phone, component_origin):
    """Ticking "Other" opens its text box without moving the checkbox."""
    page = phone
    service_page(page, component_origin)
    other = show(page, page.get_by_label("Other", exact=True))
    describe = page.get_by_label("Please describe your talent")
    stays_put(page, page.get_by_label("Other", exact=True), other.check, describe)
    stays_put(
        page,
        page.get_by_label("Other", exact=True),
        lambda: page.get_by_label("Other", exact=True).uncheck(),
    )
    expect(describe).to_have_count(0)


def test_household_status_keeps_the_page_still(phone, component_origin):
    """Choosing a household change keeps the status menu where it was."""
    from .test_family_response import form_payload
    from .test_member_census import start

    page = phone
    form = form_payload()
    form["content"]["member_census"] = "<p>Please check each person.</p>" * 30
    start(page, component_origin, form=form)
    status = show(page, page.get_by_label("Household status"))
    expect(status).to_be_visible()
    page.on("dialog", lambda dialog: dialog.accept())
    stays_put(
        page,
        page.get_by_label("Household status"),
        lambda: status.select_option("moved_household"),
    )
    expect(page.get_by_label("Household status")).to_have_value("moved_household")


def test_add_member_opens_the_new_page_at_its_top(phone, component_origin):
    """Adding a person shows the new page from the top, first name focused."""
    from .test_family_response import form_payload
    from .test_member_census import start

    page = phone
    form = form_payload()
    form["content"]["member_census"] = "<p>Please check each person.</p>" * 30
    start(page, component_origin, form=form)
    add = show(
        page,
        page.get_by_role(
            "button", name="Add a household member", exact=True, include_hidden=True
        ),
    )
    expect(add).to_be_visible()
    assert centered(page, add) > 0
    add.click()
    first = page.locator('input[id$="-first_name"]:focus')
    expect(first).to_have_count(1)
    assert page.evaluate("window.scrollY") == 0


KEPT_HEIGHT = "() => document.getElementById('family-flow').style.minHeight"


def test_the_kept_height_ends_with_the_page_and_never_reaches_review(
    phone, component_origin
):
    """The height kept for a still page stays on that page only (#384 L5).

    Moving to another page drops it, and so does every other screen: Review
    (and Thank you) would otherwise end in a screenful of blank space after
    a tick on the last page.
    """
    from .test_family_response import visit_every_page
    from .test_family_service import SERVE

    page = phone
    service_page(page, component_origin, last=True)
    show(page, page.get_by_label(SERVE)).check()
    assert page.evaluate(KEPT_HEIGHT).endswith("px")
    # A page change drops it.
    page.locator("[data-page-back]").click()
    assert page.evaluate(KEPT_HEIGHT) == ""
    # On the last page, a tick keeps it again; Review drops it.
    visit_every_page(page, required=False)
    lock = page.get_by_label(SERVE)
    assert lock.is_visible(), "the Member page is this form's last page"
    lock.uncheck()
    assert page.evaluate(KEPT_HEIGHT).endswith("px")
    page.get_by_role("button", name="Review response").click()
    expect(page.get_by_role("button", name="Submit to Sample Parish")).to_be_visible()
    assert page.evaluate(KEPT_HEIGHT) == ""
