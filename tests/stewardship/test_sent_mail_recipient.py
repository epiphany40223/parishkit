"""Sent mail's Recipient: the field's browser check against the server's (#1562).

The field's ``pattern`` (``RECIPIENT_PATTERN``) holds Apply filters before
a name is sent (R40b); the server's ``recipient_scope`` stays the
authority. The pattern must never refuse a value the server admits, or a
correct filter could not be applied.
"""

import re

import pytest

from parishkit.stewardship.jobs.delivery_metadata import (
    RECIPIENT_PATTERN,
    recipient_scope,
)

ADMITTED = (
    "12345",
    "12,345",
    " 12345 ",
    "first.head@example.org",
    "FIRST.Head@Example.ORG",
    "0f8fad5b-d9cb-469f-a165-70867728950e",
    "0F8FAD5BD9CB469FA16570867728950E",
    "{0f8fad5b-d9cb-469f-a165-70867728950e}",
    "urn:uuid:0f8fad5b-d9cb-469f-a165-70867728950e",
)
NAMES = ("Thompson", "O'Brien", "Smith, John", "Thompson Family", "García")
# Let through by the coarse pattern, then refused by the server at the
# field, in place (a short hex-like name, a malformed DUID).
SERVER_ONLY = ("Abbe", "Dee", ",", "1,2")


@pytest.mark.parametrize("value", ADMITTED)
def test_the_pattern_admits_what_the_server_admits(value):
    """Each Recipient the server reads, the field lets through as typed."""
    assert recipient_scope(value) is not None
    assert re.fullmatch(RECIPIENT_PATTERN, value)


@pytest.mark.parametrize("value", NAMES)
def test_a_name_is_refused_by_both(value):
    """A name is no Recipient: the field holds Apply, the server refuses."""
    assert re.fullmatch(RECIPIENT_PATTERN, value) is None
    with pytest.raises(ValueError):
        recipient_scope(value)


@pytest.mark.parametrize("value", SERVER_ONLY)
def test_the_server_refuses_what_the_pattern_lets_through(value):
    """The pattern is coarse: these pass it, and the server refuses them."""
    assert re.fullmatch(RECIPIENT_PATTERN, value)
    with pytest.raises(ValueError):
        recipient_scope(value)
