"""Every conditional gate reads a rule's value the same way (#953 review).

``data-show-when``, ``data-enabled-when`` and ``data-required-when`` all
compare a number field's value as a number (so "04321" is 4321), all accept
"|" alternatives, and a negated rule ("name!=a|b") holds while the control
has none of them.
"""

import pytest

from .waits import hidden, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

PATH = "/conditional-rule-values"
# A bare form under the shared script; nothing here is a real page.
PAGE = """<!doctype html>
<html lang="en"><head><title>Rules</title>
<script src="/static/stewardship/ui-v1.js" defer></script></head>
<body><form data-require-complete>
<div><label for="number">Number</label>
<input type="number" id="number" name="number" value="7"></div>
<div><label for="shown">Shown</label>
<input id="shown" name="shown" data-show-when="number=4321|5"></div>
<div><label for="enabled">Enabled</label>
<input id="enabled" name="enabled" data-enabled-when="number!=4321|5"></div>
<div><label for="needed">Needed</label>
<input id="needed" name="needed" data-required-when="number=4321|5"></div>
<button type="submit">Save</button><p data-complete-hint hidden></p>
</form></body></html>"""


def test_show_enabled_and_required_gates_compare_numbers_alike(page, component_origin):
    """A number typed with a leading zero, or matching either alternative,
    holds for all three gates at once; a negated rule needs none of them."""
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.route(
        component_origin + PATH,
        lambda route: route.fulfill(
            status=200, content_type="text/html; charset=utf-8", body=PAGE
        ),
    )
    page.goto(component_origin + PATH)
    number = page.get_by_label("Number")
    shown = page.locator("#shown")
    enabled = page.locator("#enabled")
    needed = page.locator("#needed")

    def choose(value):
        """Type ``value`` and send the change the gates listen for."""
        number.fill(value)
        number.dispatch_event("change")

    page.wait_for_function("document.querySelector('#enabled').disabled === false")
    hidden(shown)
    assert not needed.evaluate("node => node.required")
    for value in ("04321", "4321", "5", "5.0"):
        choose(value)
        visible(shown)
        assert enabled.is_disabled(), value
        assert needed.evaluate("node => node.required"), value
    for value in ("6", ""):
        choose(value)
        hidden(shown)
        assert enabled.is_enabled(), value
        assert not needed.evaluate("node => node.required"), value
    assert failures == []
