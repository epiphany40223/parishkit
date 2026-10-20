"""Shared format rules: one definition for the server and the browser (#1577).

Every ``web.field_rules.FieldRule`` has accepted and refused examples in
``fixtures/field_rule_cases.json``. Here each example is run through the
rule as Python reads it and through the real form field the rule guards,
so the server refuses exactly what the rule refuses, in the rule's words
and code; ``browser/test_field_rules.py`` runs the same table through
every browser engine against the drawn field's ``pattern``.
"""

import json
import random
import re
from pathlib import Path

import pytest
from django import forms
from django.template.loader import render_to_string

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.campaign_family_test import parse_family_duids
from parishkit.stewardship.accounts.content_forms import SUBJECT_RULE, ContentForm
from parishkit.stewardship.accounts.integration_forms import (
    KEY_RULES,
    SLACK_CHANNEL,
    InlineCredentialForm,
    IntegrationForm,
)
from parishkit.stewardship.accounts.parish_views import (
    GIVING,
    PHONE,
    WEBSITE,
    ParishForm,
)
from parishkit.stewardship.accounts.setup_forms import (
    IDENTITY_LINES,
    SetupAccessForm,
    SetupMailForm,
    SetupParishForm,
    SetupSlackForm,
    SetupTestingForm,
)
from parishkit.stewardship.accounts.share_forms import SHARE_LABEL, ShareOptionForm
from parishkit.stewardship.schema_primitives import typed
from parishkit.stewardship.web.field_rules import (
    EMAIL,
    ERROR_LINE_TEMPLATE,
    RULES,
    SPACE,
)
from parishkit.stewardship.web.presentation import parse_us_phone
from parishkit.stewardship.web.sender_name_field import SENDER_NAME

CASES = json.loads(
    (Path(__file__).parent / "fixtures" / "field_rule_cases.json").read_text()
)
# A Parish profile the form accepts; each case replaces one field of it.
PROFILE = {
    "name": "Sample Parish",
    "website": "https://example.org/",
    "timezone": "America/New_York",
    "phone": "(212) 555-1234",
    "online_giving_url": "",
    "date_format": "",
    "base_digest": "0" * 64,
}
MAIL = {
    "delegated_email": "mail@example.org",
    "sender": "mail@example.org",
    "reply_to": "mail@example.org",
}
# The form and field each rule guards, and the data it needs besides.
GUARDS = {
    PHONE.name: (ParishForm, "phone", PROFILE),
    WEBSITE.name: (ParishForm, "website", PROFILE),
    GIVING.name: (ParishForm, "online_giving_url", PROFILE),
    SLACK_CHANNEL.name: (SetupSlackForm, "channel_id", {"enabled": "on"}),
    EMAIL.name: (SetupTestingForm, "testing_recipient", {}),
    IDENTITY_LINES.name: (SetupAccessForm, "staff_addresses", {}),
    SENDER_NAME.name: (SetupMailForm, "sender_name", MAIL),
    KEY_RULES["parishsoft"].name: (
        lambda **kwargs: InlineCredentialForm("parishsoft", **kwargs),
        "candidate",
        {"intent": "x"},
    ),
    KEY_RULES["slack"].name: (
        lambda **kwargs: InlineCredentialForm("slack", **kwargs),
        "candidate",
        {"intent": "x"},
    ),
    SUBJECT_RULE.name: (
        lambda **kwargs: ContentForm(kind="email", slot="confirmation", **kwargs),
        "subject",
        {"base_digest": "0" * 64, "html": "<p>Thanks</p>", "generate_text": "on"},
    ),
    SHARE_LABEL.name: (ShareOptionForm, "label", {}),
}
# Checks no pattern can say, with their cases in the same table: each is run
# through its server parser here and through the page script's own check in
# browser/test_field_rules.py.
CHECKS = {"family_test_duids"}
EXAMPLES = [
    (name, value, verdict == "accept")
    for name, verdicts in sorted(CASES.items())
    if name not in CHECKS
    for verdict, values in sorted(verdicts.items())
    for value in values
]


def test_every_rule_has_examples_and_a_guarded_field():
    """A new rule comes with its cases and its field here."""
    assert set(CASES) == set(RULES) | CHECKS
    assert set(RULES) == set(GUARDS)
    for verdicts in CASES.values():
        assert set(verdicts) == {"accept", "refuse"}
        assert verdicts["accept"] and verdicts["refuse"]


@pytest.mark.parametrize(("name", "value", "accepted"), EXAMPLES)
def test_the_server_refuses_exactly_what_the_rule_refuses(name, value, accepted):
    """The rule's verdict is the form's, and a refusal is in the rule's words
    and carries its one code, even when the field's own format check (a URL
    field's) refuses the value first."""
    assert RULES[name].matches(value) is accepted
    form_class, field, data = GUARDS[name]
    form = form_class(data=data | {field: value})
    # The field's own rule: the registered one, or it in the field's words.
    rule = form.fields[field].field_rule
    assert rule.pattern == RULES[name].pattern
    if accepted:
        assert field not in form.errors, form.errors
    else:
        assert form.errors[field] == [str(rule.message)]
        (error,) = form.errors.as_data()[field]
        assert error.code == rule.code


def test_space_is_exactly_pythons_whitespace():
    """SPACE is the set str.strip removes and Python's \\s matches, written
    out so the browser reads the same set (its own \\s differs)."""
    every = [chr(point) for point in range(0x10000)]
    spaces = [char for char in every if re.fullmatch(f"[{SPACE}]", char)]
    assert spaces == [char for char in every if char.isspace()]
    assert spaces == [char for char in every if re.fullmatch(r"\s", char)]


@pytest.mark.parametrize("value", CASES[PHONE.name]["accept"])
def test_every_admitted_telephone_is_stored_as_e164(value):
    """parse_us_phone reads every telephone the rule admits."""
    assert re.fullmatch(r"\+1[2-9][0-9]{2}[2-9][0-9]{6}", parse_us_phone(value))


def test_a_stored_or_shown_telephone_follows_the_rule():
    """The stored E.164 value and the way the form shows it are both valid,
    so an unchanged form is never held."""
    assert PHONE.matches("+12125551234")
    field = ParishForm().fields["phone"]
    assert PHONE.matches(field.prepare_value("+12125551234"))


@pytest.mark.parametrize(
    ("form", "name", "rule"),
    [
        (ParishForm(), "phone", PHONE),
        (ParishForm(), "website", WEBSITE),
        (ParishForm(), "online_giving_url", GIVING),
        (SetupParishForm(), "phone", PHONE),
        (SetupParishForm(), "website", WEBSITE),
        (SetupSlackForm(), "channel_id", SLACK_CHANNEL),
        (IntegrationForm("slack"), "channel_id", SLACK_CHANNEL),
    ],
)
def test_the_field_draws_the_rule_and_a_reserved_error_line(form, name, rule):
    """The widget carries the rule's pattern and words, and the field group
    keeps an empty, hidden error line for the page script's message."""
    field = form.fields[name]
    attrs = field.widget.attrs
    assert attrs["pattern"] == rule.pattern
    assert attrs["data-field-rule"] == rule.name
    assert field.template_name == ERROR_LINE_TEMPLATE
    html = str(form[name].as_field_group())
    assert f'<ul class="errorlist" id="id_{name}_error" hidden><li></li></ul>' in html
    assert 'class="field-error-line" role="status"' in html
    if name != "channel_id":
        assert attrs["data-missing-hint"] == rule.message


def test_setup_keeps_its_tips_beside_a_ruled_field():
    """A ruled field still moves its long setup help into a tip, with its
    one-line hint visible (accounts.field_tips)."""
    form = SetupParishForm()
    html = str(form["phone"].as_field_group())
    assert 'id="id_phone_tip"' in html
    assert "Ten-digit US number, for example (212) 555-1234." in html
    assert form.fields["phone"].template_name == ERROR_LINE_TEMPLATE


def test_setup_slack_says_what_an_empty_channel_needs():
    """An empty channel names the choice; a malformed one, the rule."""
    attrs = SetupSlackForm().fields["channel_id"].widget.attrs
    assert attrs["data-empty-hint"] == (
        "Enter the Slack channel ID, or untick Enable Slack notifications."
    )
    assert attrs["data-missing-hint"] == SLACK_CHANNEL.message


def test_parish_settings_save_waits_for_valid_fields():
    """Parish settings' Save is held by the complete gate as well as the
    change gate, with its hint after Save and describing it."""
    html = render_to_string(
        "stewardship/parish-settings.html",
        {
            "form": ParishForm(initial=PROFILE),
            "parish_name": "Sample Parish",
            "page_url": "/p",
            "refresh_url": "/p",
            "errors": [],
        },
    )
    (form,) = re.findall(r'<form id="settings-form"[^>]*>', html)
    assert 'data-require-change="base_digest"' in form
    assert "data-require-complete" in form
    (button,) = re.findall(r"<button[^>]*>(?=Save<)", html)
    assert "settings-complete-hint" in button
    hint = '<p class="help" id="settings-complete-hint" data-complete-hint hidden>'
    assert hint in html


# A host the server's URL validator admits that a parish might have saved
# before the rule existed (or by hand in the configuration).
STORED_HOSTS = [
    "https://192.168.1.10/",
    "http://127.0.0.1:8000",
    "https://[2001:db8::1]:8443/",
    "https://[::1]",
    "https://localhost",
    "https://LocalHost:3000/",
    "https://example.org./",
    "https://example.org.:8080/",
]


def old_server_accepts(value, kind):
    """Whether the server's own checks (URL field, then the stored-value
    check) admit ``value``: what a parish could save before the shared rule."""
    try:
        text = forms.URLField(assume_scheme="https").clean(value)
        typed(text, kind)
    except (forms.ValidationError, ConfigError):
        return False
    return kind == "url" or text.lower().startswith("https://")


@pytest.mark.parametrize("form_class", [ParishForm, SetupParishForm])
@pytest.mark.parametrize("stored", STORED_HOSTS)
def test_a_stored_host_never_blocks_an_unrelated_change(form_class, stored):
    """A saved website or giving link with an IP address, ``localhost`` or a
    trailing dot still validates when only another field changes: the whole
    form is checked on every save, changed or not."""
    data = PROFILE | {
        "website": stored,
        "online_giving_url": stored.replace("http://", "https://"),
        "date_format": "eu_long",
    }
    form = form_class(data=data)
    assert form.is_valid(), form.errors


def values():
    """Hosts of every kind, valid and not, with and without a port, a
    trailing dot and a path, from a fixed seed so a failure is repeatable."""
    rnd = random.Random(1577)
    pieces = [
        "example", "ex-ample", "-x", "x-", "a" * 63, "a" * 64, "m\u00fcnchen",
        "org", "c", "xn--p1ai", "ORG", "0", "1", "255", "256", "01", "localhost",
        "LOCALHOST", "", "::1", "2001:db8::1", "1::2::3", "::ffff:1.2.3.4",
        "g::1", "12345::1", "1111:2222:3333:4444:5555:6666:1.2.3.4",
        "ffff:ffff:ffff:ffff:ffff:ffff:255.255.255.255",
    ]  # fmt: skip
    for _ in range(2000):
        host = rnd.choice([".", ".", ":"]).join(
            rnd.choice(pieces) for _ in range(rnd.randint(1, 4))
        )
        if rnd.random() < 0.3:
            host = f"[{host}]"
        yield "https://" + host + rnd.choice(["", "", ".", ":80", ":x", "/", "/a b"])


def test_the_host_rules_agree_with_what_the_server_accepted_before():
    """In both directions: a value the server's checks admitted, the rules
    admit (so no stored value is newly refused), and a value they refused,
    the rules refuse (the browser never admits what the server refuses)."""
    checked = admitted = 0
    for value in [*values(), *STORED_HOSTS]:
        checked += 1
        old = old_server_accepts(value, "url")
        admitted += old
        assert WEBSITE.matches(value) is old, value
        assert GIVING.matches(value) is old_server_accepts(value, "link"), value
    # The generator reaches both verdicts for a real share of its values.
    assert 50 < admitted < checked - 100


# Every field that takes one email address, and the words it says.
EMAIL_FIELDS = [
    (SetupMailForm, "delegated_email", "Enter a valid delegated mailbox address."),
    (SetupMailForm, "sender", "Enter a valid From address."),
    (SetupMailForm, "reply_to", "Enter a valid reply-to address."),
    (SetupTestingForm, "testing_recipient", "Enter a valid Testing email address."),
    (
        lambda: IntegrationForm("google_workspace"),
        "delegated_email",
        "Enter a valid delegated mailbox address.",
    ),
    (lambda: IntegrationForm("email"), "sender", "Enter a valid From address."),
    (lambda: IntegrationForm("email"), "reply_to", "Enter a valid reply-to address."),
]


@pytest.mark.parametrize(("form_class", "name", "message"), EMAIL_FIELDS)
def test_every_address_field_draws_the_email_rule(form_class, name, message):
    """A text box asking for an email keyboard, with the shared pattern in
    the field's own words and a reserved error line: the browser's own
    type=email check admits a@b, which the server refuses."""
    field = form_class().fields[name]
    attrs = field.widget.attrs
    assert field.widget.input_type == "text"
    assert attrs["inputmode"] == "email"
    assert attrs["pattern"] == EMAIL.pattern
    assert attrs["data-field-rule"] == EMAIL.name
    assert attrs["data-missing-hint"] == message
    assert field.template_name == ERROR_LINE_TEMPLATE


def test_new_user_email_follows_the_rule_sign_in_uses():
    """New user's Email draws the rule in its own words, and clean_email
    (what Review and pk-admin users apply) admits exactly the table's
    accepted examples."""
    from parishkit.stewardship.accounts.user_changes import (
        MESSAGES,
        UserRefused,
        clean_email,
    )
    from parishkit.stewardship.accounts.user_views import UserForm

    attrs = UserForm().fields["email"].widget.attrs
    assert attrs["pattern"] == EMAIL.pattern
    assert attrs["inputmode"] == "email"
    assert attrs["data-missing-hint"] == MESSAGES["email_invalid"]
    assert attrs["data-empty-hint"] == MESSAGES["email_required"]
    for verdict, values in CASES[EMAIL.name].items():
        for value in values:
            try:
                clean_email(value)
            except UserRefused:
                accepted = False
            else:
                accepted = True
            assert accepted is (verdict == "accept"), value


def generated(pieces, seed, count, joiner=""):
    """``count`` values made of ``pieces`` from a fixed seed."""
    rnd = random.Random(seed)
    for _ in range(count):
        yield joiner.join(rnd.choice(pieces) for _ in range(rnd.randint(0, 6)))


def test_the_email_rule_is_djangos_email_validator():
    """In both directions, over generated addresses with every kind of user
    part and domain: the rule admits exactly what EmailValidator admits,
    including what IGNORECASE folds to ASCII letters, once stripped as
    the field strips it."""
    from django.core.validators import EmailValidator

    validator = EmailValidator()
    users = [
        "a", "B.c", "a..b", ".a", "a+b", '"a\\ b"', '"a b"', '"a@b"', '""', "\u0130",
        "\u017fa", "\u212a", "\u00e9", "o'k", "", '"\x0b"', "a b",
    ]  # fmt: skip
    domains = [
        "example", "ex-ample", "-x", "x-", "a" * 63, "a" * 64, "m\u00fcnchen", "org",
        "c", "xn--p1ai", "XN--\u212a", "co", "\u2003a", "", "0", "localhost",
        "[1.2.3.4]", "[01.2.3.4]", "[::1]", "[IPv6:::1]",
    ]  # fmt: skip
    rnd = random.Random(1577)
    values = (
        rnd.choice(users)
        + rnd.choice(["@", "@", "@", "", "@@"])
        + ".".join(rnd.choice(domains) for _ in range(rnd.randint(1, 3)))
        + rnd.choice(["", "", ".", " "])
        for _ in range(20000)
    )
    admitted = checked = 0
    for value in values:
        if not value.strip():
            # Empty once stripped: the required rule's to judge.
            continue
        try:
            validator(value.strip())
        except forms.ValidationError:
            old = False
        else:
            old = True
        checked += 1
        admitted += old
        assert EMAIL.matches(value) is old, value
    assert 50 < admitted < checked - 50


def test_the_address_list_rule_is_identity_lines():
    """The list rule admits exactly what Setup › Access's field admits."""
    field = SetupAccessForm().fields["staff_addresses"]
    pieces = [
        "a@example.org", "b@example.org", "a@b", "\n", "\r\n", " ", "\x1c",
        " ", "\t", "　", '"x\x0by"@example.org', "x@example.o ", ",",
    ]  # fmt: skip
    admitted = checked = 0
    for value in generated(pieces, 1577, 5000):
        try:
            field.clean(value)
        except forms.ValidationError:
            old = False
        else:
            old = True
        checked += 1
        admitted += old
        assert IDENTITY_LINES.matches(value) is old, repr(value)
    assert 50 < admitted < checked - 50


def test_the_sender_name_rule_is_clean_sender_name():
    """The From name rule refuses exactly the characters clean_sender_name
    does (control and format characters by Python's own Unicode data), on
    the stripped value the field cleans."""
    import unicodedata

    from parishkit.stewardship.sender_name import clean_sender_name

    for point in range(0x110000):
        char = chr(point)
        if 0xD800 <= point <= 0xDFFF:
            continue
        refused = char in '<>@"\\' or unicodedata.category(char) in {"Cc", "Cf"}
        assert SENDER_NAME.matches(f"a{char}b") is not refused, hex(point)
    for value in generated(["a", " ", "\t", "<", "‮", " "], 1577, 2000):
        try:
            clean_sender_name(value.strip())
        except ValueError:
            old = False
        else:
            old = True
        assert SENDER_NAME.matches(value) is old, repr(value)


@pytest.mark.parametrize("target", sorted(KEY_RULES))
def test_a_key_the_rule_admits_is_one_its_installer_reads(target):
    """Every key the table admits, stripped as the field strips it, is one
    the target's installer accepts, and every key it refuses (other than by
    length alone) the installer refuses too."""
    from parishkit.stewardship.accounts.cryptography import CryptographicError
    from parishkit.stewardship.accounts.integration_candidates import (
        slack_candidate,
    )
    from parishkit.stewardship.source.credentials import SourceCredential

    def installer_reads(value):
        """Whether the target's own reader takes the pasted bytes."""
        try:
            data = value.encode("utf-8")
            if target == "parishsoft":
                SourceCredential(data)
            else:
                slack_candidate(data)
        except (CryptographicError, ConfigError):
            return False
        return True

    rule = KEY_RULES[target]
    for verdict, values in CASES[rule.name].items():
        for value in values:
            if value.strip():  # Only spaces is no key: blank keeps the saved one.
                assert installer_reads(value.strip()) is (verdict == "accept"), value


def validate_subject(value):
    """Whether the server's template check admits ``value`` as a subject."""
    from parishkit.stewardship.web.content import validate_template

    try:
        validate_template(value, subject=True)
    except ValueError:
        return False
    return True


def validate_label(value):
    """Whether the server's label checks admit ``value`` once stripped."""
    from parishkit.stewardship.schema_primitives import text
    from parishkit.stewardship.web.content import validate_share_label

    value = value.strip()
    try:
        text(value, 1024)
        validate_share_label(value)
    except (ValueError, ConfigError):
        return False
    return True


TEMPLATE_PIECES = [
    "{", "}", "{{", "}}", "%", "#", "{%", "%}", "{#", "#}", " ", "\t", "　",
    "\x1c", "a", "{{ parish_name }}", "{{pronoun}}", "{{ family_code }}",
    "{{ nope }}", "{{　campaign_year\t}}", "{{ file.a }}", "{{ fi", "le.a }}",
    "\x00", "\x85", "\x7f", "{{ financial_period }}",
]  # fmt: skip


def test_the_subject_rule_is_validate_template():
    """In both directions: a subject the rule admits is one the server's
    template check admits, and the reverse."""
    for value in generated(TEMPLATE_PIECES, 1577, 20000):
        assert SUBJECT_RULE.matches(value) is validate_subject(value), repr(value)


def test_the_share_label_rule_is_the_label_checks():
    """In both directions, but one: the old checks took a hosted-file
    placeholder that only forms once a placeholder inside it is taken out
    ("{{ fi{{ pronoun }}le.a }}"); the rule refuses that, and the server now
    checks the rule first."""
    for value in generated(TEMPLATE_PIECES, 1577, 20000):
        if not value.strip():
            continue
        old = validate_label(value)
        if old and "{{ fi" in value and not SHARE_LABEL.matches(value):
            continue
        assert SHARE_LABEL.matches(value) is old, repr(value)


@pytest.mark.parametrize(
    "value",
    [
        ("a@example.org\n\n\n\n" * 49) + "a@example.org\n\n!",
        " \n" * 6000 + "x",
        "a" * 12000,
        "a." * 6000 + "@",
        "a@" + "a-" * 6000,
        "a@b.co" + " " * 6000 + "!",
    ],
)
def test_a_refused_list_is_answered_at_once(value):
    """Long values the address rules refuse take no noticeable time: the
    patterns read a run of spaces or blank lines one way only."""
    import time

    start = time.monotonic()
    assert not IDENTITY_LINES.matches(value)
    assert not EMAIL.matches(value)
    assert time.monotonic() - start < 1


@pytest.mark.parametrize(
    ("value", "accepted"),
    [
        (value, verdict == "accept")
        for verdict, values in CASES["family_test_duids"].items()
        for value in values
    ],
)
def test_the_duid_list_examples_get_the_servers_verdict(value, accepted):
    """The DUID list cases, which ui-v1.js's data-duid-list check also
    answers in the browser test, are the server parser's verdicts."""
    try:
        parse_family_duids(value)
    except ValueError:
        assert not accepted
    else:
        assert accepted


def test_test_with_families_sends_the_parsers_rules():
    """The DUID field carries the server's own token rule, limit and words."""
    import json as json_module

    from parishkit.stewardship.accounts.campaign_family_test import duid_list_rules
    from parishkit.stewardship.accounts.campaign_family_test_views import (
        FamilyTestForm,
    )

    attrs = FamilyTestForm().fields["families"].widget.attrs
    assert json_module.loads(attrs["data-duid-list"]) == duid_list_rules()
    assert duid_list_rules()["limit"] == 10


def test_a_financial_period_end_is_marked_one_year():
    """Both financial period ends are drawn data-one-year, so the page
    makes the day the server requires their min and max; the campaign end
    only keeps its order."""
    from parishkit.stewardship.accounts.campaign_forms import CampaignForm

    form = CampaignForm()
    for name in ("financial_end", "comparison_end"):
        assert form.fields[name].widget.attrs["data-one-year"] is True
    assert "data-one-year" not in form.fields["end_date"].widget.attrs


def test_every_upload_refuses_an_empty_file_in_its_words():
    """Each upload field says why a 0-byte file is refused, in the browser
    (data-empty-file-message) and on the server (its "empty" refusal)."""
    from django.core.files.uploadedfile import SimpleUploadedFile

    from parishkit.stewardship.accounts.artwork_views import ArtworkForm
    from parishkit.stewardship.accounts.branding_views import LogoUploadForm
    from parishkit.stewardship.accounts.hosted_file_views import UploadForm
    from parishkit.stewardship.accounts.setup_branding_views import SetupLogoForm
    from parishkit.stewardship.web.field_rules import EMPTY_FILE

    fields = [
        (ArtworkForm, "image"),
        (LogoUploadForm, "logo"),
        (UploadForm, "file"),
        (SetupLogoForm, "logo"),
    ]
    for form_class, name in fields:
        field = form_class().fields[name]
        assert field.widget.attrs["data-empty-file-message"] == EMPTY_FILE
        with pytest.raises(forms.ValidationError) as refused:
            field.clean(SimpleUploadedFile("empty.png", b""))
        assert refused.value.messages == [str(EMPTY_FILE)]
        assert refused.value.code == "empty"
    for slot in LogoUploadForm().slot_fields:
        assert slot.field.widget.attrs["data-empty-file-message"] == EMPTY_FILE
