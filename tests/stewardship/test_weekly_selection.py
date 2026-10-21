"""A sent Weekly digest's retained observation decodes into validated values."""

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from parishkit.stewardship.reports.weekly_digest import (
    WeeklyCorrection,
    WeeklyInformation,
)
from parishkit.stewardship.reports.weekly_observation import (
    WeeklyUnavailable,
    decode_observation,
)
from parishkit.stewardship.reports.weekly_selection import (
    WeeklyItem,
    WeeklyObservation,
    WeeklySelection,
)

NOW = datetime(2026, 9, 17, 12, tzinfo=UTC)
CAMPAIGN = UUID(int=1)


def item(sequence, *, disposition="current_actionable", identifier=None):
    """Use equal timestamps deliberately; interval ordering comes from sequence."""
    identity = identifier or UUID(int=sequence + 100), sequence, "Example", NOW
    value = (
        WeeklyInformation(*identity, f"Private request {sequence}")
        if disposition == "current_actionable"
        else WeeklyCorrection(*identity, disposition)
    )
    return WeeklyItem(sequence, value)


def observation(*items, watermark=None):
    """Capture a synthetic closed projection without touching the database."""
    return WeeklyObservation(
        CAMPAIGN,
        uuid4(),
        uuid4(),
        NOW,
        watermark
        if watermark is not None
        else max((v.sequence for v in items), default=0),
        tuple(items),
    )


def document():
    """Represent only the SQL projection, not a browser-supplied selection."""
    return {
        "campaign_id": str(CAMPAIGN),
        "source_id": str(uuid4()),
        "configuration_id": str(uuid4()),
        "observed_at": NOW.isoformat(),
        "watermark": 2,
        "items": [
            [str(UUID(int=101)), 1, 42, "Example", NOW.isoformat(), "withdrawn", None],
            [
                str(UUID(int=102)),
                2,
                42,
                "Example",
                NOW.isoformat(),
                "current_actionable",
                "Private request",
            ],
        ],
    }


def test_values_are_immutable_and_repr_does_not_show_private_values():
    value = item(1)
    observed = observation(value)
    selected = WeeklySelection(observed, (value.value,), ())
    for obj in (value, observed, selected):
        assert "Private request" not in repr(obj)
        assert "Example" not in repr(obj)
        assert str(CAMPAIGN) not in repr(obj)
    with pytest.raises(FrozenInstanceError):
        observed.watermark = 2


@pytest.mark.parametrize("sequence", [True, 0, -1, 1.0, "1", None])
def test_invalid_item_sequence(sequence):
    with pytest.raises(ValueError):
        WeeklyItem(sequence, item(1).value)


def test_invalid_item_type():
    with pytest.raises(ValueError):
        WeeklyItem(1, {"text": "private"})


@pytest.mark.parametrize(
    "change",
    [
        {"campaign_id": "private"},
        {"source_id": None},
        {"configuration_id": True},
        {"observed_at": NOW.replace(tzinfo=None)},
        {"observed_at": "private"},
        {"watermark": True},
        {"watermark": -1},
        {"watermark": 0},
        {"items": []},
        {"items": (None,)},
        {"items": (item(1), item(1))},
        {"items": (item(2), item(1)), "watermark": 2},
        {"items": (item(1), item(2, identifier=UUID(int=101))), "watermark": 2},
        {"observed_at": NOW - timedelta(seconds=1)},
    ],
)
def test_invalid_observation_is_rejected(change):
    with pytest.raises(ValueError):
        replace(observation(item(1)), **change)


def test_decode_copies_private_mutable_projection():
    source = document()
    result = decode_observation(source)
    source["items"][1][-1] = "MUTATED"
    assert result.items[1].value.text == "Private request"
    assert result.items[0].value.disposition == "withdrawn"
    assert not hasattr(result.items[0].value, "text")


@pytest.mark.parametrize(
    "field,value",
    [
        ("watermark", True),
        ("watermark", -1),
        ("campaign_id", "PRIVATE-CANARY"),
        ("source_id", None),
        ("configuration_id", 5),
        ("observed_at", "PRIVATE-CANARY"),
        ("observed_at", NOW.replace(tzinfo=None).isoformat()),
        ("observed_at", 42),
        ("items", ()),
        ("unknown", "PRIVATE-CANARY"),
    ],
)
def test_decode_rejects_invalid_envelope_without_private_values(field, value):
    source = document()
    source[field] = value
    with pytest.raises(WeeklyUnavailable, match="inputs are unavailable") as error:
        decode_observation(source)
    assert "PRIVATE-CANARY" not in str(error.value)
    assert error.value.__suppress_context__


@pytest.mark.parametrize(
    "row",
    [
        None,
        {},
        ["PRIVATE-CANARY"],
        [
            str(UUID(int=101)),
            1,
            42,
            "Example",
            NOW.isoformat(),
            "withdrawn",
            "PRIVATE-CANARY",
        ],
        [str(UUID(int=101)), 1, 42, "Example", NOW.isoformat(), "unknown", None],
        [
            str(UUID(int=101)),
            1,
            42,
            "Example",
            NOW.isoformat(),
            "current_actionable",
            None,
        ],
    ],
)
def test_decode_rejects_invalid_or_text_bearing_corrections(row):
    source = document()
    source["items"] = [row]
    with pytest.raises(WeeklyUnavailable) as error:
        decode_observation(source)
    assert "PRIVATE-CANARY" not in str(error.value)


@pytest.mark.parametrize("source", [None, [], {}, "PRIVATE-CANARY"])
def test_decode_requires_exact_envelope(source):
    with pytest.raises(WeeklyUnavailable):
        decode_observation(source)
