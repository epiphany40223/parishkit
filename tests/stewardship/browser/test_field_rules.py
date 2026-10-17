"""Shared format rules in real browsers (#1577).

Each rule's examples (``fixtures/field_rule_cases.json``, which the unit
test runs through the server's own form fields) get the same verdict from
the drawn field's ``pattern`` in every engine. Setup › Parish profile keeps
Save and continue unavailable while the telephone is malformed, with the
rule's words as its reason, and says them at the field once it is left, in a
reserved line that moves nothing; a valid number releases both. Parish
settings' Save waits the same way. The server's refusal of a forged post is
covered beside the views (``database/test_setup_views_postgresql.py``).
"""

import json
from pathlib import Path

import pytest

from .waits import has_attribute, has_text, hidden, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)
CASES = json.loads(
    (Path(__file__).parents[1] / "fixtures" / "field_rule_cases.json").read_text()
)
# Where each rule's field is drawn: the page and the field's label.
FIELDS = {
    "parish_phone": ("/setup-parish", "Parish telephone"),
    "parish_website": ("/setup-parish", "Parish website"),
    "online_giving_url": ("/setup-parish", "Online giving URL"),
    "slack_channel_id": ("/setup-slack", "Slack channel ID"),
}
PHONE_RULE = "Enter a ten-digit US telephone number, such as (212) 555-1234."
# The browser's whole verdict on a field's current value.
VALID = "field => field.validity.valid"


def watch(page):
    """Fail on any script error."""
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    return failures


def spot(locator):
    """The element's page position in whole pixels, to show it did not move."""
    return tuple(
        round(value)
        for value in locator.evaluate(
            "e => { const box = e.getBoundingClientRect();"
            " return [box.left + window.scrollX, box.top + window.scrollY]; }"
        )
    )


@pytest.mark.parametrize("name", sorted(FIELDS))
def test_every_example_gets_the_servers_verdict(page, component_origin, name):
    """The drawn pattern admits exactly the examples the server admits."""
    assert set(FIELDS) == set(CASES)
    failures = watch(page)
    address, label = FIELDS[name]
    page.goto(component_origin + address)
    if name == "slack_channel_id":
        page.get_by_label("Enable Slack notifications").check()
    field = page.get_by_label(label)
    for verdict, values in CASES[name].items():
        for value in values:
            field.fill(value)
            assert field.evaluate(VALID) is (verdict == "accept"), (value, verdict)
    assert not failures


def test_a_malformed_telephone_holds_save_and_continue(page, component_origin):
    """Setup › Parish profile: a malformed telephone keeps Save and continue
    unavailable with the rule's words after it, and the same words show at
    the field once it is left; a valid number releases both, and nothing
    moves as the message comes and goes."""
    failures = watch(page)
    page.goto(component_origin + "/setup-parish")
    page.get_by_label("Parish name").fill("Sample Parish")
    page.get_by_label("Parish website").fill("https://example.org/")
    page.get_by_label("Parish timezone").select_option("America/New_York")
    save = page.get_by_role("button", name="Save and continue")
    hint = page.locator("#setup-complete-hint")
    phone = page.get_by_label("Parish telephone")
    line = page.locator("#id_phone_error")
    where = spot(save)
    phone.fill("212-555-123")
    assert save.is_disabled()
    has_text(hint, PHONE_RULE)
    assert "setup-complete-hint" in save.get_attribute("aria-describedby").split()
    hidden(line)
    phone.press("Tab")
    has_attribute(phone, "aria-invalid", "true")
    visible(line)
    has_text(line, PHONE_RULE)
    assert "id_phone_error" in phone.get_attribute("aria-describedby").split()
    assert spot(save) == where
    phone.fill("(212) 555-1234")
    has_attribute(phone, "aria-invalid", "false")
    hidden(line)
    assert "id_phone_error" not in (phone.get_attribute("aria-describedby") or "")
    assert save.is_enabled()
    hidden(hint)
    assert spot(save) == where
    assert not failures


def test_parish_settings_save_waits_for_a_valid_telephone(page, component_origin):
    """Parish settings: a changed but malformed telephone keeps Save
    unavailable with the rule's words; a valid one releases it."""
    failures = watch(page)
    page.goto(component_origin + "/parish-settings")
    save = page.get_by_role("button", name="Save", exact=True)
    hint = page.locator("#settings-complete-hint")
    phone = page.get_by_label("Parish telephone")
    phone.fill("555-1234")
    assert save.is_disabled()
    has_text(hint, PHONE_RULE)
    phone.press("Tab")
    has_text(page.locator("#id_phone_error"), PHONE_RULE)
    phone.fill("(212) 555-9876")
    assert save.is_enabled()
    hidden(hint)
    hidden(page.locator("#id_phone_error"))
    assert not failures
