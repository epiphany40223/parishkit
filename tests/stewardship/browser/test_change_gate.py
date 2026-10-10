"""The change gate shares its submit buttons with the other gates (#921).

A synthetic page loads ui-v1.js with three forms marked data-require-change:
one whose button the acknowledgment gate also holds, one whose button the
server drew disabled, and one whose button came back disabled without the
server's mark (as Firefox restores a script's disabled on a reload).
The acknowledgment boxes carry no name, so ticking one is not a change.
"""

import pytest

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

ADDRESS = "/change-gate-fixture"
PAGE = """<!doctype html><html lang="en"><head><title>Change gate</title>
<script src="/static/stewardship/ui-v1.js" defer></script></head><body>
<form id="both" method="post" action="/nowhere" data-require-change="v">
<input type="hidden" name="v" value="1">
<label for="both-name">Both name</label><input id="both-name" name="name" value="a">
<input type="checkbox" id="both-ack" data-acknowledgment>
<label for="both-ack">Both understood</label>
<fieldset disabled><label for="both-off">Both off</label>
<input id="both-off" name="off" value="x"></fieldset>
<button type="submit">Both</button>
<span data-unchanged-hint data-idle>Change both.</span>
</form>
<form id="server" method="post" action="/nowhere" data-require-change="v">
<input type="hidden" name="v" value="1">
<label for="server-name">Server name</label>
<input id="server-name" name="name" value="a">
<input type="checkbox" id="server-ack" data-acknowledgment>
<label for="server-ack">Server understood</label>
<button type="submit" disabled data-server-disabled>Locked</button>
</form>
<form id="restored" method="post" action="/nowhere" data-require-change="v">
<input type="hidden" name="v" value="1">
<label for="restored-name">Restored name</label>
<input id="restored-name" name="name" value="a">
<button type="submit" disabled>Restored</button>
<span data-unchanged-hint data-idle>Change restored.</span>
</form>
</body></html>"""


@pytest.fixture
def gate_page(page, component_origin):
    """The synthetic page, served at ``ADDRESS`` beside ui-v1.js."""
    page.route(
        component_origin + ADDRESS,
        lambda route: route.fulfill(content_type="text/html", body=PAGE),
    )
    page.goto(component_origin + ADDRESS)
    return page


def test_a_button_two_gates_hold_waits_for_both(gate_page):
    """The button is enabled only while neither the change gate nor the
    acknowledgment gate holds it; releasing one leaves the other's hold."""
    from playwright.sync_api import expect

    page = gate_page
    button = page.get_by_role("button", name="Both")
    name = page.get_by_label("Both name")
    ack = page.get_by_label("Both understood")
    expect(button).to_be_disabled()
    ack.check()
    expect(button).to_be_disabled()
    name.fill("b")
    expect(button).to_be_enabled()
    ack.uncheck()
    expect(button).to_be_disabled()
    name.fill("a")
    ack.check()
    expect(button).to_be_disabled()
    name.fill("b")
    expect(button).to_be_enabled()
    # A held button tells Firefox not to bring its disabled back on reload.
    expect(button).to_have_attribute("autocomplete", "off")


def test_a_field_in_a_disabled_fieldset_is_no_change(gate_page):
    """A control inside a disabled fieldset is not sent, so its value never
    makes Review available."""
    from playwright.sync_api import expect

    page = gate_page
    page.get_by_label("Both understood").check()
    page.locator("#both-off").evaluate(
        "field => { field.value = 'y'; field.dispatchEvent("
        "new Event('input', {bubbles: true})); }"
    )
    expect(page.get_by_role("button", name="Both")).to_be_disabled()


def test_no_gate_enables_a_button_the_server_disabled(gate_page):
    """A change and an acknowledgment never enable the server's disabled
    button, nor does undoing them."""
    from playwright.sync_api import expect

    page = gate_page
    button = page.get_by_role("button", name="Locked")
    expect(button).to_be_disabled()
    page.get_by_label("Server understood").check()
    page.get_by_label("Server name").fill("b")
    expect(button).to_be_disabled()
    page.get_by_label("Server name").fill("a")
    page.get_by_label("Server understood").uncheck()
    page.get_by_label("Server understood").check()
    page.get_by_label("Server name").fill("b")
    expect(button).to_be_disabled()


def test_a_restored_disabled_button_is_gated_again(gate_page):
    """A button that came back disabled without the server's mark is
    enabled as drawn, then held by the change gate like any other: a change
    enables it. Each form's hint gets its own id, from its form's."""
    from playwright.sync_api import expect

    page = gate_page
    button = page.get_by_role("button", name="Restored")
    expect(button).to_be_disabled()
    expect(button).to_have_attribute("data-change-gated", "")
    expect(button).to_have_accessible_description("Change restored.")
    page.get_by_label("Restored name").fill("b")
    expect(button).to_be_enabled()
    page.get_by_label("Both name").fill("b")
    expect(page.locator("#both-unchanged-hint")).to_have_text("Change both.")
    expect(page.locator("#restored-unchanged-hint")).to_have_text("Change restored.")
