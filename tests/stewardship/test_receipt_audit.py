"""Receipt skip diagnostics retain a closed reason, never source or answer values."""

import pytest

from parishkit.stewardship.audit.schemas import ContextKind, sanitize


def test_receipt_skip_reason_is_closed_operational_metadata():
    """A no-recipient outcome is neither a provider failure nor private prose."""
    context = {"reason": "no_deliverable_recipient", "recipient_count": 0}
    assert sanitize(ContextKind.EMAIL, context) == context


@pytest.mark.parametrize("value", ["private@example.org", "", None, True, 0, []])
def test_receipt_reason_cannot_be_a_private_value(value):
    """Reason-shaped keys do not grant arbitrary operational payload storage."""
    with pytest.raises(ValueError):
        sanitize(ContextKind.EMAIL, {"reason": value})
