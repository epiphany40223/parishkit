"""Schedule-selection audit contains typed identities and counts, never content."""

from uuid import uuid4

import pytest

from parishkit.stewardship.audit.schemas import ContextKind, sanitize


def test_schedule_context_accepts_only_typed_metadata():
    """UUID conversion is explicit; count values remain machine-readable integers."""
    identifier = uuid4()
    assert sanitize(
        ContextKind.SCHEDULE, {"definition_id": identifier, "cancelled_messages": 12}
    ) == {
        "definition_id": str(identifier),
        "cancelled_messages": 12,
    }


@pytest.mark.parametrize("value", ["PRIVATE", True, -1, 1.5, 2**63, None])
def test_schedule_counts_cannot_carry_private_text_or_coerced_values(value):
    with pytest.raises(ValueError):
        sanitize(ContextKind.SCHEDULE, {"cancelled_messages": value})


@pytest.mark.parametrize("field", ["recipient", "subject", "reason", "code", "token"])
def test_schedule_context_has_no_free_text_fields(field):
    with pytest.raises(ValueError):
        sanitize(ContextKind.SCHEDULE, {field: "PRIVATE"})
