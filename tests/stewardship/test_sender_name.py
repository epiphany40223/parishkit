"""The optional From display name: validation, fallback and header encoding."""

import smtplib
from contextlib import nullcontext
from email import message_from_bytes, policy
from email.utils import parseaddr
from types import SimpleNamespace
from uuid import uuid4

import pytest

from parishkit.stewardship.accounts.integration_forms import IntegrationForm
from parishkit.stewardship.accounts.setup_forms import SetupMailForm, validate_values
from parishkit.stewardship.family_delivery import (
    FamilyDeliveryMail,
    deliver_family,
    delivery_settings,
)
from parishkit.stewardship.readiness_mail import ReadinessMail
from parishkit.stewardship.sender_name import (
    apply_sender_name,
    clean_sender_name,
    resolved_sender_name,
)

SETTINGS = {
    "delegated_email": "mail@example.org",
    "sender": "office@example.org",
    "reply_to": "reply@example.org",
}


@pytest.mark.parametrize(
    "value,expected",
    [
        ("  St. Example   Stewardship ", "St. Example Stewardship"),
        ("", ""),
        ("Parroquia Sagrada Familia – Corresponsabilidad", None),
        ("Smith, Jones & Co.", None),
        ("x" * 100, None),
    ],
)
def test_plain_names_are_accepted_and_whitespace_is_collapsed(value, expected):
    """Unicode, commas and ampersands are fine; spacing is normalized."""
    assert clean_sender_name(value) == (value if expected is None else expected)


@pytest.mark.parametrize(
    "value",
    [
        "x" * 101,
        "Parish <evil@example.org>",
        "office@example.org",
        'Quote " name',
        "Back\\slash",
        "Line\nBreak: injected",
        "Bidi‮override",
        "Zero​width",
        "Bell\x07",
        None,
    ],
)
def test_names_that_could_read_as_addresses_or_headers_are_refused(value):
    """Line breaks, format characters, <, >, @, quotes and overlong names fail."""
    with pytest.raises(ValueError):
        clean_sender_name(value)


def test_blank_or_unusable_names_fall_back_to_the_parish_name():
    """An explicit name wins; otherwise the Parish name; otherwise nothing."""
    assert resolved_sender_name("Stewardship Team", "St. Example") == (
        "Stewardship Team"
    )
    assert resolved_sender_name("", "St. Example") == "St. Example"
    assert resolved_sender_name(None, "St. Example") == "St. Example"
    assert resolved_sender_name("", "Parish <x@example.org>") == ""
    assert resolved_sender_name(None, None) == ""


@pytest.mark.parametrize(
    "name",
    ["St. Example Stewardship", "Smith, Jones & Co.", "Parroquia Señora de Guadalupe"],
)
def test_from_header_encodes_and_quotes_the_display_name(name):
    """SMTP serialization quotes specials and RFC 2047-encodes non-ASCII names."""
    message = ReadinessMail(
        uuid4(),
        "office@example.org",
        "reply@example.org",
        "test@example.org",
        "Subject",
        "<p>Body</p>",
        "Body",
        sender_name=name,
    ).message()
    raw = message.as_bytes(policy=policy.SMTP)
    assert raw.isascii()
    header = next(
        line for line in raw.decode().splitlines() if line.startswith("From:")
    )
    if not name.isascii():
        assert "=?utf-8?" in header
    if "," in name:
        assert f'"{name}"' in header
    parsed = message_from_bytes(raw, policy=policy.default)
    assert parsed["From"].addresses[0].display_name == name
    assert parsed["From"].addresses[0].addr_spec == "office@example.org"
    assert parseaddr(str(parsed["From"]))[1] == "office@example.org"


def test_without_a_name_the_from_header_is_the_bare_address():
    """An empty resolved name leaves the historical bare address."""
    mail = ReadinessMail(
        uuid4(),
        "office@example.org",
        "reply@example.org",
        "test@example.org",
        "Subject",
        "<p>Body</p>",
        "Body",
    )
    assert mail.message()["From"] == "office@example.org"
    assert ReadinessMail.from_payload(mail.payload()) == mail
    legacy = {
        key: value for key, value in mail.payload().items() if key != "sender_name"
    }
    assert ReadinessMail.from_payload(legacy) == mail
    with pytest.raises(ValueError):
        ReadinessMail(**{**mail.__dict__, "sender_name": "a@b"})


def test_apply_replaces_the_single_from_header():
    """Exactly one From header remains after applying a name."""
    mail = FamilyDeliveryMail(
        uuid4(),
        SETTINGS["sender"],
        SETTINGS["reply_to"],
        ("family@example.org",),
        "Renewal",
        "<p>Hi</p>",
        "Hi",
    )
    message = apply_sender_name(mail.message(), "St. Example", SETTINGS["sender"])
    # "." is special in a display name, so the name is quoted.
    assert message.get_all("From") == ['"St. Example" <office@example.org>']


def test_delivery_settings_accept_only_a_clean_optional_name():
    """The helper's closed settings admit the name but no other extra field."""
    assert delivery_settings(SETTINGS) == SETTINGS
    named = SETTINGS | {"sender_name": "St. Example"}
    assert delivery_settings(named) == named
    for bad in (
        SETTINGS | {"sender_name": "a@b.example"},
        SETTINGS | {"sender_name": 3},
        SETTINGS | {"other": "x"},
    ):
        with pytest.raises(ValueError):
            delivery_settings(bad)


def test_family_submission_sends_the_display_name(monkeypatch):
    """The submitted DATA carries the name; the envelope keeps the bare address."""
    sent = {}
    monkeypatch.setattr(
        "parishkit.stewardship.family_delivery._credentials",
        lambda *args: SimpleNamespace(token="synthetic-token"),
    )

    class SMTP:
        """Accept every command and keep the submitted message bytes."""

        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def ehlo(self):
            return 250, b""

        def docmd(self, *args):
            return 235, b""

        def has_extn(self, name):
            return False

        def mail(self, sender, *, options):
            sent["envelope"] = sender
            return 250, b""

        def rcpt(self, address):
            return 250, b""

        def data(self, content):
            sent["data"] = content
            return 250, b""

    mail = FamilyDeliveryMail(
        uuid4(),
        SETTINGS["sender"],
        SETTINGS["reply_to"],
        ("family@example.org",),
        "Renewal",
        "<p>Hi</p>",
        "Hi",
    )
    deliver_family(
        b"synthetic-key",
        SETTINGS | {"sender_name": "Smith, Jones & Co."},
        mail,
        smtp_factory=SMTP,
        session_factory=nullcontext,
    )
    assert sent["envelope"] == "office@example.org"
    parsed = message_from_bytes(sent["data"], policy=policy.default)
    assert parsed["From"].addresses[0].display_name == "Smith, Jones & Co."
    assert smtplib  # The fake stands in for smtplib.SMTP_SSL.


def test_setup_mail_form_validates_and_defaults_the_name():
    """The wizard stores a cleaned name or ""; legacy drafts read as ""."""
    base = {
        "delegated_email": "mail@example.org",
        "sender": "mail@example.org",
        "reply_to": "office@example.org",
    }
    form = SetupMailForm(base | {"sender_name": "  St.  Example  "})
    assert form.is_valid() and form.cleaned_data["sender_name"] == "St. Example"
    form = SetupMailForm(base | {"sender_name": "Evil <a@b.example>"})
    assert not form.is_valid() and "sender_name" in form.errors
    assert validate_values("mail", base)["sender_name"] == ""


def test_integration_form_omits_an_empty_name():
    """A blank From name is absent from the applied settings (Parish fallback)."""
    data = {
        "base_digest": "a" * 64,
        "sender": "Office@Example.org",
        "reply_to": "reply@example.org",
    }
    form = IntegrationForm("email", data | {"sender_name": ""})
    assert form.public_settings() == {
        "sender": "office@example.org",
        "reply_to": "reply@example.org",
    }
    form = IntegrationForm("email", data | {"sender_name": "St. Example"})
    assert form.public_settings()["sender_name"] == "St. Example"
    assert not IntegrationForm("email", data | {"sender_name": "x\ny"}).is_valid()
