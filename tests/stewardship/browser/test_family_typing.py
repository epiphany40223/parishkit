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
