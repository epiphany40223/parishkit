"""Operational identifiers are useful without becoming private-value storage."""

import pytest

from parishkit.stewardship.audit.schemas import (
    MEMBER_SOURCE_FIELDS,
    ContextKind,
    sanitize,
)
from parishkit.stewardship.responses.member_census import MEMBER_FIELDS


def test_diagnostic_field_vocabulary_matches_closed_census_registry():
    """Keep the privacy allowlist complete without allowing arbitrary field text."""
    assert {field.name for field in MEMBER_FIELDS} == MEMBER_SOURCE_FIELDS
    for field in MEMBER_SOURCE_FIELDS:
        context = {"family_duid": 1, "member_duid": 3, "field": field}
        assert sanitize(ContextKind.MEMBER_SOURCE, context) == context


@pytest.mark.parametrize(
    "context",
    [
        {},
        {"family_duid": 1, "member_duid": 3},
        {"family_duid": 1, "member_duid": 3, "field": "private-value"},
        {"family_duid": 1, "member_duid": 3, "field": "email", "value": "private"},
        *[
            {"family_duid": value, "member_duid": 3, "field": "email"}
            for value in (True, 0, -1, 2**31, "1", 1.0, None)
        ],
    ],
)
def test_diagnostic_context_rejects_private_or_invalid_values(context):
    """Reject missing identifiers, type confusion, unbounded IDs and free text."""
    with pytest.raises(ValueError):
        sanitize(ContextKind.MEMBER_SOURCE, context)
