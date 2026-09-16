"""Closed command validation rejects malformed input before policy/database work."""

from uuid import uuid4

import pytest

from parishkit.stewardship.jobs.delivery_admin import (
    clear_recipient_refusal,
    evidence_note,
)
from parishkit.stewardship.jobs.delivery_metadata import family_duid
from parishkit.stewardship.jobs.delivery_resolution import resolve_delivery


@pytest.mark.parametrize("value", [None, 1, True, "", " \t\n", "x" * 2001, "a\x00b"])
def test_evidence_is_required_bounded_and_private(value):
    """Error messages must not interpolate the rejected private evidence."""
    with pytest.raises(ValueError, match="bounded verification note") as error:
        evidence_note(value)
    assert str(error.value) == "A bounded verification note is required."


def test_evidence_retains_exact_intent_including_whitespace():
    """Replay comparison must retain rather than normalize the submitted note."""
    assert evidence_note(" Confirmed\n") == " Confirmed\n"


@pytest.mark.parametrize("value", ["12345", "12,345", "00012345"])
def test_copied_family_identifier_accepts_display_grouping(value):
    """The human-formatted US identifier remains a usable exact search key."""
    assert family_duid(value) == 12345


@pytest.mark.parametrize(
    "value",
    [
        None,
        True,
        12,
        "",
        "0",
        "12,34",
        "01,234",
        "1,234,",
        "-1",
        "١٢",
        " 12",
        "9" * 26,
        str(2**63),
    ],
)
def test_family_identifier_rejects_malformed_or_out_of_range_values(value):
    """Grouping cannot smuggle non-identifiers into database predicates."""
    with pytest.raises(ValueError):
        family_duid(value)


def test_missing_retry_keys_fail_before_database_access():
    """Preparation rejects missing prerequisites with a stable validation error."""
    from parishkit.stewardship.jobs.delivery_resolution import _prepare

    with pytest.raises(ValueError, match="current public/general keys and origin"):
        _prepare(None, general=None, public=None, public_origin=None)


@pytest.mark.parametrize(
    "override",
    [
        dict(message_id="not-an-id"),
        dict(command_id=None),
        dict(expected_version=True),
        dict(expected_version=0),
        dict(expected_version=2**63),
        dict(action=[]),
        dict(action="erase"),
        dict(action="resend"),
        dict(duplicate_acknowledged=1),
        dict(note=""),
        dict(preparation_inputs="invalid"),
        dict(preparation_inputs=lambda: {}, public_origin="http://localhost"),
    ],
)
def test_resolution_rejects_invalid_intent_before_database(override):
    """No configured store, SQL connection, provider, or key is needed to reject."""
    values = dict(
        message_id=uuid4(),
        command_id=uuid4(),
        expected_version=1,
        action="note",
        note="Confirmed",
        duplicate_acknowledged=False,
    )
    with pytest.raises(ValueError):
        resolve_delivery(None, uuid4(), **(values | override))


@pytest.mark.parametrize(
    "override",
    [
        dict(refusal_id="not-an-id"),
        dict(source_snapshot_id=None),
        dict(verified=False),
        dict(source_generation=True),
        dict(source_generation=0),
        dict(source_generation=2**63),
        dict(note=""),
    ],
)
def test_clearance_rejects_invalid_intent_before_database(override):
    """Source identity and explicit verification cannot be truthy approximations."""
    values = dict(
        refusal_id=uuid4(),
        command_id=uuid4(),
        source_snapshot_id=uuid4(),
        source_generation=1,
        verified=True,
        note="Confirmed",
    )
    with pytest.raises(ValueError):
        clear_recipient_refusal(None, uuid4(), **(values | override))
