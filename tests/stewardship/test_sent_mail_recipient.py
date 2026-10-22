"""Sent mail's Recipient: the field's browser check against the server's (#1562).

The field's ``pattern`` (``RECIPIENT_PATTERN``) holds Apply filters before
a malformed DUID or address is sent (R40b); any other text is a name
(#1849). The server's ``recipient_scope`` stays the authority. The pattern
must never refuse a value the server admits, or a correct filter could not
be applied.
"""

import re

import pytest

from parishkit.stewardship.jobs.delivery_metadata import (
    RECIPIENT_PATTERN,
    recipient_scope,
)

ADMITTED = (
    ("12345", "duid"),
    ("12,345", "duid"),
    (" 12345 ", "duid"),
    ("first.head@example.org", "address"),
    ("FIRST.Head@Example.ORG", "address"),
    ("0f8fad5b-d9cb-469f-a165-70867728950e", "id"),
    ("0F8FAD5BD9CB469FA16570867728950E", "id"),
    ("{0f8fad5b-d9cb-469f-a165-70867728950e}", "id"),
    ("urn:uuid:0f8fad5b-d9cb-469f-a165-70867728950e", "id"),
    ("Thompson", "name"),
    ("O'Brien", "name"),
    ("Smith, John", "name"),
    ("Thompson Family", "name"),
    ("García", "name"),
    ("Abbe", "name"),
    ("Leader 9", "name"),
)
# Refused by both: digits and commas that are no DUID, an "@" that is no
# one address.
REFUSED = (
    "1,2",
    ",",
    "12,34",
    "12 34",
    "1, 234",
    "a@b@c",
    "@example.org",
    "Smith@",
    "a b@c",
)
# Let through by the pattern, then refused by the server at the field, in
# place: a DUID out of range.
SERVER_ONLY = ("0", "9" * 25 + "9")


@pytest.mark.parametrize(("value", "kind"), ADMITTED)
def test_the_pattern_admits_what_the_server_admits(value, kind):
    """Each Recipient the server reads, the field lets through as typed."""
    assert recipient_scope(value)[0] == kind
    assert re.fullmatch(RECIPIENT_PATTERN, value)


@pytest.mark.parametrize("value", REFUSED)
def test_a_malformed_duid_or_address_is_refused_by_both(value):
    """The field holds Apply, and the server refuses it too."""
    assert re.fullmatch(RECIPIENT_PATTERN, value) is None
    with pytest.raises(ValueError):
        recipient_scope(value)


@pytest.mark.parametrize("value", SERVER_ONLY)
def test_the_server_refuses_what_the_pattern_lets_through(value):
    """The pattern checks a DUID's shape only; the server its range too."""
    assert re.fullmatch(RECIPIENT_PATTERN, value)
    with pytest.raises(ValueError):
        recipient_scope(value)


def test_a_name_keeps_its_text_as_typed():
    """A name is searched as typed, trimmed; the server compares it ignoring
    case, so it is not lower-cased here."""
    assert recipient_scope("  Thompson ") == ("name", "Thompson")
