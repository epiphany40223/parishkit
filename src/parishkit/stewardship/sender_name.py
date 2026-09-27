"""The optional display name shown with every stewardship email's From address.

Mail programs show a bare From address as just the address. Parishes can set
an optional From name in the outgoing-mail settings; when it is blank the
Parish profile name is used. The name is only presentation: routing, SQL
checks and retained outbox rows keep comparing the bare address, and the
name is applied when the message is built for submission.
"""

import unicodedata
from email.headerregistry import Address

MAX_SENDER_NAME = 100
# Characters that would let a display name impersonate or confuse an address.
_FORBIDDEN = frozenset('<>@"\\')


def clean_sender_name(value):
    """Return a safe single-line display name ("" when blank) or raise ValueError.

    Control and format characters (including line breaks and bidirectional
    overrides), angle brackets, "@", quotes and backslashes are refused so the
    name can never read as, or smuggle, another address or header.
    """
    if type(value) is not str or any(
        char in _FORBIDDEN or unicodedata.category(char) in {"Cc", "Cf"}
        for char in value
    ):
        raise ValueError("Use a plain one-line From name without < > @.")
    # Only runs of ordinary spaces remain to tidy; line breaks were refused.
    value = " ".join(value.split())
    if len(value) > MAX_SENDER_NAME:
        raise ValueError("Use a From name of at most 100 characters.")
    return value


def resolved_sender_name(configured, parish_name):
    """The configured From name, else the Parish name; "" when neither is usable."""
    for value in (configured, parish_name):
        try:
            name = clean_sender_name(value or "")
        except ValueError:
            continue
        if name:
            return name
    return ""


def from_header(name, address):
    """A From header value: an encoded display name with the address, or the address.

    email.headerregistry quotes commas and other specials and RFC 2047-encodes
    non-ASCII names when the message is serialized for SMTP.
    """
    if not name:
        return address
    username, _, domain = address.rpartition("@")
    return Address(display_name=name, username=username, domain=domain)


def apply_sender_name(message, name, address):
    """Replace a built message's bare From address with its display form."""
    del message["From"]
    message["From"] = from_header(name, address)
    return message


def configured_sender_name(configuration_id):
    """Read the applied From name, falling back to the applied Parish name."""
    from .accounts.configuration_models import AppliedIntegration, Parish

    settings = (
        AppliedIntegration.objects.filter(
            configuration_id=configuration_id, kind="email"
        )
        .values_list("settings", flat=True)
        .first()
        or {}
    )
    parish = (
        Parish.objects.filter(configuration_id=configuration_id)
        .values_list("name", flat=True)
        .first()
    )
    return resolved_sender_name(settings.get("sender_name"), parish)
