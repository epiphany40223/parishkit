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

from .waits import greyed, has_attribute, has_text, hidden, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)
CASES = json.loads(
    (Path(__file__).parents[1] / "fixtures" / "field_rule_cases.json").read_text()
)
# Where each rule's field (or dedicated check's) is drawn: the page and the
# field's label.
FIELDS = {
    "parish_phone": ("/setup-parish", "Parish telephone"),
    "parish_website": ("/setup-parish", "Parish website"),
    "online_giving_url": ("/setup-parish", "Online giving URL"),
    "slack_channel_id": ("/setup-slack", "Slack channel ID"),
    "email_address": ("/setup-testing", "Testing email address"),
    "email_lines": ("/setup-access", "Staff email addresses"),
    "sender_name": ("/setup-mail", "From name"),
    "parishsoft_key": ("/setup-credential", "ParishSoft API key"),
    "slack_token": ("/setup-credential-slack", "Slack bot token"),
    "email_subject": ("/content-settings", "Email subject"),
    "share_label": ("/share-settings", "Option label"),
    "share_short_label": ("/share-settings", "Short label"),
    "family_test_duids": (
        "/family-tests-check",
        "Family DUIDs, one per line (at most ten)",
    ),
}
PHONE_RULE = "Enter a ten-digit US telephone number, such as (212) 555-1234."
# The browser's whole verdict on a field's current value.
VALID = "field => field.validity.valid"
# Puts a case's exact value in the field, as typing would, then lets the
# page's input listeners see it. Playwright's fill() in Firefox drops C0
# control characters such as U+001C, so the server-refused "212\x1c555-1234"
# arrived as a valid "212555-1234".
ENTER = """(field, value) => {
  field.value = value;
  field.dispatchEvent(new Event("input", {bubbles: true}));
}"""


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
    """The drawn pattern (or the page script's own check, for a DUID
    list) admits exactly the examples the server admits."""
    assert set(FIELDS) == set(CASES)
    failures = watch(page)
    address, label = FIELDS[name]
    page.goto(component_origin + address)
    if name == "slack_channel_id":
        page.get_by_label("Enable Slack notifications").check()
    field = page.get_by_label(label).first
    multiline = field.evaluate("field => field.matches('textarea')")
    for verdict, values in CASES[name].items():
        for value in values:
            if not multiline and ("\n" in value or "\r" in value):
                # A text box drops line breaks from any value it is given,
                # so it can never send one.
                continue
            field.evaluate(ENTER, value)
            # A textarea holds each line break as "\n" (and sends "\r\n").
            held = value.replace("\r\n", "\n").replace("\r", "\n")
            assert field.input_value() == (held if multiline else value)
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


def test_unchanged_words_leave_the_live_region_alone(page, component_origin):
    """While a left field stays invalid, more typing keeps the same words, so
    the polite live region is not rewritten (a screen reader would read them
    again); a valid value then clears it."""
    failures = watch(page)
    page.goto(component_origin + "/setup-parish")
    phone = page.get_by_label("Parish telephone")
    line = page.locator("#id_phone_error")
    phone.fill("212")
    phone.press("Tab")
    has_text(line, PHONE_RULE)
    line.evaluate(
        "line => { window.ruleWrites = 0; new MutationObserver((records) =>"
        " { window.ruleWrites += records.length; }).observe(line,"
        " {childList: true, subtree: true, characterData: true,"
        " attributes: true}); }"
    )
    phone.press_sequentially("-55")
    has_text(line, PHONE_RULE)
    assert page.evaluate("window.ruleWrites") == 0
    phone.fill("(212) 555-1234")
    hidden(line)
    assert page.evaluate("window.ruleWrites") > 0
    assert not failures


def test_a_malformed_from_address_holds_save_and_continue(page, component_origin):
    """Setup › Outgoing email: an address the browser's own email check
    would take (a@b) but the server refuses keeps Save and continue
    unavailable with the field's words, said at the field once left."""
    failures = watch(page)
    page.goto(component_origin + "/setup-mail")
    page.get_by_label("Delegated mailbox").first.fill("mail@example.org")
    page.get_by_label("Reply-to address").first.fill("mail@example.org")
    sender = page.get_by_label("From address").first
    save = page.get_by_role("button", name="Save and continue")
    hint = page.locator("#setup-complete-hint")
    sender.fill("parish@example")
    assert save.is_disabled()
    has_text(hint, "Enter a valid From address.")
    sender.press("Tab")
    has_text(page.locator("#id_sender_error"), "Enter a valid From address.")
    sender.fill('"Parish\\ office"@example.org')
    has_attribute(sender, "aria-invalid", "false")
    hidden(page.locator("#id_sender_error"))
    assert save.is_enabled()
    assert not failures


@pytest.mark.parametrize("value", ["\x85", "\x1c \u3000"])
def test_only_pythons_whitespace_is_still_empty(page, component_origin, value):
    """A required address holding only what the server strips (U+0085 and
    U+001C included, which JavaScript's trim() keeps) is empty, so Save and
    continue waits for it, as the server's required check refuses it."""
    failures = watch(page)
    page.goto(component_origin + "/setup-testing")
    address = page.get_by_label("Testing email address").first
    address.evaluate(ENTER, value)
    assert page.get_by_role("button", name="Save and continue").is_disabled()
    assert not failures


def test_an_address_list_line_is_checked_at_its_field(page, component_origin):
    """Setup › Access: a textarea of addresses follows the list rule as it
    is typed; a malformed line is said at the field, and fixing it clears
    the field's mark on the same keystroke."""
    failures = watch(page)
    page.goto(component_origin + "/setup-access")
    staff = page.get_by_label("Staff email addresses").first
    line = page.locator("#id_staff_addresses_error")
    save = page.get_by_role("button", name="Save and continue")
    staff.fill("a@example.org\nnot an address")
    assert save.is_disabled()
    staff.press("Tab")
    has_text(line, "Enter one email address on each line, at most fifty.")
    staff.fill("a@example.org\nb@example.org")
    has_attribute(staff, "aria-invalid", "false")
    hidden(line)
    assert save.is_enabled()
    assert not failures


def test_check_these_families_waits_for_distinct_duids(page, component_origin):
    """Test with Families: one Family twice (as 1,234 and 1234) keeps Check
    these Families unavailable with the server's words; distinct DUIDs
    release it."""
    failures = watch(page)
    page.goto(component_origin + "/family-tests-check")
    field = page.get_by_label("Family DUIDs, one per line (at most ten)")
    check = page.get_by_role("button", name="Check these Families")
    hint = page.locator("#family-test-check-hint")
    field.fill("1,234\n1234")
    assert check.is_disabled()
    has_text(hint, "Enter up to ten distinct Family DUIDs.")
    field.fill("1,234 x")
    has_text(hint, "Use an exact Family DUID.")
    field.fill("1,234\n5678")
    assert check.is_enabled()
    assert not failures


def test_a_financial_period_end_must_be_one_year_on(page, component_origin):
    """Campaign settings: a financial period's end may only be the day
    before its start's first anniversary; that day is its min and max."""
    failures = watch(page)
    page.goto(component_origin + "/campaign-settings")
    page.locator("#id_financial_enabled").check()
    page.get_by_label("Upcoming financial period start").fill("2028-02-29")
    end = page.get_by_label("Upcoming financial period end")
    has_attribute(end, "min", "2029-02-27")
    has_attribute(end, "max", "2029-02-27")
    end.fill("2029-02-28")
    assert end.evaluate("field => field.validity.rangeOverflow")
    end.fill("2029-02-27")
    assert end.evaluate(VALID)
    assert not failures


def test_an_empty_logo_file_is_refused_at_once(page, component_origin):
    """Parish logos: a 0-byte file is refused as soon as it is chosen, in
    the server's words, in the field's reserved line, and Upload waits."""
    failures = watch(page)
    page.goto(component_origin + "/branding-settings")
    logo = page.get_by_label("Parish logo").first
    logo.set_input_files({"name": "logo.png", "mimeType": "image/png", "buffer": b""})
    line = page.locator("#id_logo_error")
    has_text(line, "The chosen file is empty (0 bytes). Choose another file.")
    greyed(page.get_by_role("button", name="Upload and review logos"))
    assert not failures


def test_an_unknown_subject_placeholder_holds_preview(page, component_origin):
    """Pages and emails: a subject naming a placeholder that doesn't exist
    keeps Preview changes unavailable with the subject rule's words."""
    failures = watch(page)
    page.goto(component_origin + "/content-settings")
    subject = page.get_by_label("Email subject").first
    preview = page.get_by_role("button", name="Preview changes")
    subject.fill("Thanks, {{ nope }}")
    assert preview.is_disabled()
    has_text(
        page.locator("#content-complete-hint"),
        "Use only the documented placeholders, on one line.",
    )
    subject.fill("Thanks, {{ parish_name }}")
    assert preview.is_enabled()
    assert not failures


def test_a_private_share_label_placeholder_holds_save(page, component_origin):
    """Share options: a label naming a Family's code keeps Save unavailable
    with the label rule's words."""
    failures = watch(page)
    page.goto(component_origin + "/share-settings")
    label = page.get_by_label("Option label").first
    save = page.get_by_role("button", name="Save", exact=True)
    label.fill("Code {{ family_code }}")
    assert save.is_disabled()
    has_text(
        page.locator("#share-complete-hint"),
        "Use visible text and parish_name, pronoun, campaign_year, "
        "financial_period, financial_start or financial_end placeholders.",
    )
    label.fill("Other gift to {{ parish_name }}")
    assert save.is_enabled()
    assert not failures
