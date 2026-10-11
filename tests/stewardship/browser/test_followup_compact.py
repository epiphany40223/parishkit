"""The Ministry follow-up request form is compact and steady (#1014).

Short fields share a row at desktop width (Status and Outcome; How, Date and
Time), the notes stay full width, editing the form moves nothing, and at
phone width every field takes its own row.
"""

import pytest

from .waits import visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "webkit"], indirect=True
)

FORM = "#followup-item form"
# The form's height at 1280 px, with every field shown. Before #1014 it was
# about 1,295 px once Resolved and a contact channel were chosen (700 px
# with those fields hidden); with the short fields side by side it is about
# 830 px, whatever is chosen.
MAX_HEIGHT = 860
# Page coordinates of every label and visible control in the form, so a
# scroll does not count as a layout change.
LAYOUT = """() => [...document.querySelectorAll('#followup-item form :is('
    + 'label, legend, input:not([type=hidden]), select, textarea, button)'
)].map((item) => {
    const box = item.getBoundingClientRect();
    return [item.id || item.textContent.trim(), box.x + scrollX, box.y + scrollY,
        box.width, box.height];
})"""
AXE = """async () => (await axe.run(document, {
    runOnly: {type: 'tag', values: ['wcag2a','wcag2aa','wcag21aa','wcag22aa']}
})).violations.map(({id,impact}) => ({id,impact}))"""
ROWS = (
    ("#followup-state", "#followup-outcome"),
    ("#contact-channel", "#contact-date", "#contact-time"),
)


def form_height(page):
    """The follow-up form's rendered height, once it is shown."""
    form = page.locator(FORM)
    visible(form)
    return form.bounding_box()["height"]


def boxes(page, selectors):
    """The bounding boxes of the controls ``selectors`` names, in order."""
    return [page.locator(selector).bounding_box() for selector in selectors]


def test_followup_form_is_compact_at_desktop_width(page, component_origin, axe_source):
    """At 1280 px the short fields share rows, the notes span the form, and
    the whole form stays within its height bound."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + "/followup-item")
    height = form_height(page)
    assert height <= MAX_HEIGHT, height
    for row in ROWS:
        tops = {round(box["y"]) for box in boxes(page, row)}
        assert len(tops) == 1, (row, tops)
    # Each notes box spans its fieldset's content width, as its legend does.
    for notes in ("#followup-notes", "#contact-notes"):
        widths = page.evaluate(
            """(id) => {
                const area = document.querySelector(id);
                const legend = area.closest('fieldset').querySelector('legend');
                return [area.getBoundingClientRect().width,
                    legend.getBoundingClientRect().width];
            }""",
            notes,
        )
        # Two pixels of slack: WebKit draws a textarea a little narrower.
        assert abs(widths[0] - widths[1]) <= 2, (notes, widths)
    page.evaluate(axe_source)
    assert page.evaluate(AXE) == []


def test_followup_form_moves_nothing_while_editing(page, component_origin):
    """Choosing a status, outcome and contact channel, typing the contact
    date, time and notes, and opening a field tip move no label or control:
    fields that do not apply stay in place, greyed, rather than appearing."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + "/followup-item")
    before = page.evaluate(LAYOUT)
    outcome = page.locator("#followup-outcome")
    assert outcome.is_visible() and outcome.is_disabled()

    def steady(step):
        """Run one edit, then check that the layout is unchanged."""
        step()
        assert page.evaluate(LAYOUT) == before, step.__doc__

    def resolve():
        """Choose Resolved."""
        page.get_by_label("Status").select_option("resolved")

    def choose_outcome():
        """Choose an outcome."""
        outcome.select_option(index=1)

    def channel():
        """Choose a contact channel."""
        page.get_by_label("How").select_option("phone")

    def date():
        """Type a past date."""
        page.locator("#contact-date").fill("2020-01-15")

    def time():
        """Type a time, which shows its reading."""
        page.locator("#contact-time").fill("9:30 am")
        page.locator("#contact-time").blur()
        visible(page.locator("#contact-time_reading").get_by_text("Reads as"))

    def notes():
        """Type notes and what happened."""
        page.locator("#followup-notes").fill("Called and left a message.")
        page.locator("#contact-notes").fill("No answer.")

    def tip():
        """Open and close the How field's tip."""
        button = page.locator("#contact-details-help").locator(
            "xpath=preceding-sibling::button"
        )
        button.click()
        visible(page.locator("#contact-details-help"))
        assert page.evaluate(LAYOUT) == before
        page.keyboard.press("Escape")

    for step in (resolve, choose_outcome, channel, date, time, notes, tip):
        steady(step)
    assert page.get_by_role("button", name="Save follow-up").is_enabled()


def test_followup_form_is_one_column_at_phone_width(page, component_origin, axe_source):
    """At 400 px every field takes its own row, one under another, and the
    page never scrolls sideways."""
    page.set_viewport_size({"width": 400, "height": 900})
    page.goto(component_origin + "/followup-item")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    for row in ROWS:
        found = boxes(page, row)
        assert len({round(box["x"]) for box in found}) == 1, (row, found)
        for upper, lower in zip(found, found[1:], strict=False):
            assert lower["y"] >= upper["y"] + upper["height"], row
    page.evaluate(axe_source)
    assert page.evaluate(AXE) == []
