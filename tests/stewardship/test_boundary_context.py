"""Boundary diagnostics are complete typed metadata, never arbitrary census text."""

from uuid import UUID

import pytest

from parishkit.stewardship.audit.schemas import ContextKind, sanitize

IDENTIFIER = UUID("7df46d16-0f95-4689-90c7-06bce9e44c33")


def context():
    """Return an independent deterministic boundary diagnostic example."""
    return {
        "occurrence_id": IDENTIFIER,
        "kind": "start",
        "intended_unix_microseconds": 1_800_000_000_000_000,
        "actual_unix_microseconds": 1_800_000_001_000_000,
        "lag_microseconds": 1_000_000,
        "before_state": "scheduled",
        "after_state": "active",
    }


INVALID = [
    ("occurrence_id", "PRIVATE"),
    ("kind", "PRIVATE"),
    ("before_state", "PRIVATE"),
    ("after_state", None),
    ("actual_unix_microseconds", True),
    ("actual_unix_microseconds", "PRIVATE"),
    ("intended_unix_microseconds", -1),
    ("lag_microseconds", -1),
    ("lag_microseconds", 1.5),
    ("token", "PRIVATE"),
]


def test_complete_boundary_context_has_no_free_text():
    """Names, codes, provider payloads and arbitrary state labels are not fields."""
    assert sanitize(ContextKind.BOUNDARY, context()) == {
        **context(),
        "occurrence_id": str(IDENTIFIER),
    }


@pytest.mark.parametrize("key,value", INVALID)
def test_invalid_boundary_context_is_rejected(key, value):
    with pytest.raises(ValueError):
        sanitize(ContextKind.BOUNDARY, {**context(), key: value})


@pytest.mark.parametrize("missing", list(context()))
def test_boundary_context_requires_every_field(missing):
    payload = context()
    del payload[missing]
    with pytest.raises(ValueError):
        sanitize(ContextKind.BOUNDARY, payload)
