"""An end date typed digit by digit keeps every digit (#1419).

An end date marked data-not-before takes its start as its min (#563).
Chromium fires the end's input event as soon as its first year digit makes
a whole date (0002-10-31), and rewriting min at that moment, even to the
same value, rebuilt the control's fields: the next digit cleared the whole
date. The page script now writes min only when the start changes it.

Engines differ in what typed keys do to a date control (see
test_system_logs' half-typed date test): Chromium builds the date from the
digits, while Playwright's WebKit builds leave it empty. Each outcome is
asserted, never skipped.
"""

import pytest

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "webkit"], indirect=True
)

# Count every write to the end's min attribute from here on.
WATCH_MIN = """end => {
  window.minWrites = 0;
  new MutationObserver((changes) => { window.minWrites += changes.length; })
    .observe(end, {attributes: true, attributeFilter: ['min']});
}"""


def test_an_end_date_typed_digit_by_digit_keeps_its_digits(
    page, component_origin, browser_engine
):
    """Typing 10312026 into the campaign end date, after its start is filled
    in, never rewrites the end's min, and Chromium ends with 2026-10-31."""
    page.goto(component_origin + "/campaign-create")
    page.get_by_label("Campaign start date").fill("2026-10-16")
    end = page.get_by_label("Campaign end date")
    assert end.get_attribute("min") == "2026-10-16"
    end.evaluate(WATCH_MIN)
    end.focus()
    values = []
    for digit in "10312026":
        page.keyboard.press(digit)
        values.append(end.input_value())
    assert page.evaluate("window.minWrites") == 0
    assert end.get_attribute("min") == "2026-10-16"
    # The year passes through 0002, 0020 and 0202 on its way to 2026.
    typed = ["", "", "", "", "0002-10-31", "0020-10-31", "0202-10-31", "2026-10-31"]
    if browser_engine.browser_type.name == "chromium":
        assert values == typed
    else:
        # WebKit either builds the date as Chromium does or ignores the keys.
        assert values in (typed, [""] * 8)
