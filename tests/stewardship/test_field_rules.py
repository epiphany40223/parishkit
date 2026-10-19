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
from parishkit.stewardship.accounts.integration_forms import (
    SLACK_CHANNEL,
    IntegrationForm,
)
from parishkit.stewardship.accounts.parish_views import (
    GIVING,
    PHONE,
    WEBSITE,
    ParishForm,
)
from parishkit.stewardship.accounts.setup_forms import SetupParishForm, SetupSlackForm
from parishkit.stewardship.schema_primitives import typed
from parishkit.stewardship.web.field_rules import ERROR_LINE_TEMPLATE, RULES, SPACE
from parishkit.stewardship.web.presentation import parse_us_phone

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
# The form and field each rule guards, and the data it needs besides.
GUARDS = {
    PHONE.name: (ParishForm, "phone", PROFILE),
    WEBSITE.name: (ParishForm, "website", PROFILE),
    GIVING.name: (ParishForm, "online_giving_url", PROFILE),
    SLACK_CHANNEL.name: (SetupSlackForm, "channel_id", {"enabled": "on"}),
}
EXAMPLES = [
    (name, value, verdict == "accept")
    for name, verdicts in sorted(CASES.items())
    for verdict, values in sorted(verdicts.items())
    for value in values
]


def test_every_rule_has_examples_and_a_guarded_field():
    """A new rule comes with its cases and its field here."""
    assert set(CASES) == set(RULES) == set(GUARDS)
    for verdicts in CASES.values():
        assert set(verdicts) == {"accept", "refuse"}
        assert verdicts["accept"] and verdicts["refuse"]


@pytest.mark.parametrize(("name", "value", "accepted"), EXAMPLES)
def test_the_server_refuses_exactly_what_the_rule_refuses(name, value, accepted):
    """The rule's verdict is the form's, and a refusal is in the rule's words
    and carries its one code, even when the field's own format check (a URL
    field's) refuses the value first."""
    rule = RULES[name]
    assert rule.matches(value) is accepted
    form_class, field, data = GUARDS[name]
    form = form_class(data=data | {field: value})
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
