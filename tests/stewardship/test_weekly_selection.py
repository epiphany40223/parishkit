"""Successful interval coverage is distinct from actual item delivery history."""

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from parishkit.stewardship.reports.weekly_digest import (
    WeeklyCorrection,
    WeeklyInformation,
    render_weekly_digest,
)
from parishkit.stewardship.reports.weekly_observation import (
    WeeklyUnavailable,
    capture_weekly_observation,
    decode_observation,
    observation_document,
)
from parishkit.stewardship.reports.weekly_selection import (
    WeeklyHistory,
    WeeklyItem,
    WeeklyObservation,
    select_weekly,
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


@pytest.mark.parametrize(
    "microseconds,fraction",
    [
        (0, ""),
        (100000, ".1"),
        (120000, ".12"),
        (123000, ".123"),
        (123400, ".1234"),
        (123450, ".12345"),
        (123456, ".123456"),
    ],
)
def test_observation_timestamp_encoding_matches_exact_sql_spelling(
    microseconds, fraction
):
    """Cheap format coverage complements the real SQL capture invariant test."""
    instant = datetime(2026, 9, 17, microsecond=microseconds, tzinfo=UTC)
    row = item(1)
    row = replace(row, value=replace(row.value, submitted_at=instant))
    captured = replace(observation(row), observed_at=instant)
    encoded = observation_document(captured)
    expected = f"2026-09-17T00:00:00{fraction}+00:00"
    assert encoded["observed_at"] == expected
    assert encoded["items"][0][4] == expected
    assert decode_observation(encoded) == captured


def test_first_interval_selects_only_actionable_requests():
    rows = (
        item(1, disposition="withdrawn"),
        item(2),
        item(3, disposition="superseded"),
    )
    result = select_weekly(observation(*rows), WeeklyHistory(CAMPAIGN))
    assert result.information == (rows[1].value,)
    assert result.corrections == ()
    assert result.observation.watermark == 3


def test_successful_boundary_does_not_repeat_old_or_lose_equal_timestamp_new_items():
    first, second = item(1), item(2)
    result = select_weekly(observation(first, second), WeeklyHistory(CAMPAIGN, 1))
    assert first.value.submitted_at == second.value.submitted_at
    assert result.information == (second.value,)


def test_partial_acceptance_is_not_successful_interval_progress():
    row = item(1)
    history = WeeklyHistory(CAMPAIGN, reported=frozenset({row.value.item_id}))
    result = select_weekly(observation(row), history)
    assert result.information == (row.value,)
    assert history.watermark == 0


@pytest.mark.parametrize("disposition", ["superseded", "withdrawn"])
def test_corrections_survive_later_empty_intervals_and_do_not_repeat(disposition):
    row = item(1, disposition=disposition)
    observed = observation(row, watermark=50)
    reported = frozenset({row.value.item_id})
    result = select_weekly(observed, WeeklyHistory(CAMPAIGN, 50, reported))
    assert result.corrections == (row.value,)
    assert not hasattr(result.corrections[0], "text")
    history = WeeklyHistory(
        CAMPAIGN, 50, reported, frozenset({(row.value.item_id, disposition)})
    )
    assert select_weekly(observed, history).empty


def test_unsent_withdrawn_items_do_not_fabricate_corrections():
    observed = observation(item(1, disposition="withdrawn"), watermark=10)
    result = select_weekly(observed, WeeklyHistory(CAMPAIGN, 10))
    assert result.empty
    assert result.observation.watermark == 10


def test_replacement_produces_new_request_and_correction_only_for_reported_old_item():
    old, new = item(1, disposition="superseded"), item(3)
    history = WeeklyHistory(CAMPAIGN, 2, frozenset({old.value.item_id}))
    result = select_weekly(observation(old, new), history)
    assert result.information == (new.value,)
    assert result.corrections == (old.value,)
    digest = result.document(
        snapshot_id=uuid4(),
        parish_name="Parish",
        campaign_name="Census",
        campaign_timezone="America/New_York",
    )
    rendered = render_weekly_digest(digest, public_origin="https://parish.example")
    assert "Private request 3" in rendered.text
    assert "Private request 1" not in rendered.text


def test_canonical_renderer_order_is_not_assumed_to_equal_sequence_order():
    first = item(1, identifier=UUID(int=999))
    second = item(2, identifier=UUID(int=10))
    result = select_weekly(observation(first, second), WeeklyHistory(CAMPAIGN))
    assert result.information == (second.value, first.value)


def test_empty_interval_still_has_a_capture_boundary():
    result = select_weekly(observation(watermark=9), WeeklyHistory(CAMPAIGN, 8))
    assert result.empty and result.observation.watermark == 9


def test_input_and_output_are_immutable_and_repr_does_not_show_private_values():
    value = item(1)
    observed = observation(value)
    history = WeeklyHistory(CAMPAIGN)
    selected = select_weekly(observed, history)
    for obj in (value, observed, history, selected):
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


@pytest.mark.parametrize(
    "change",
    [
        {"campaign_id": "private"},
        {"watermark": True},
        {"watermark": -1},
        {"reported": set()},
        {"reported": frozenset({"private"})},
        {"corrected": set()},
        {"corrected": frozenset({"private"})},
        {"corrected": frozenset({(uuid4(), "withdrawn")})},
        {
            "corrected": frozenset({(UUID(int=101), "current_actionable")}),
            "reported": frozenset({UUID(int=101)}),
        },
    ],
)
def test_invalid_history_is_rejected(change):
    with pytest.raises(ValueError):
        replace(WeeklyHistory(CAMPAIGN), **change)


@pytest.mark.parametrize(
    "history", [WeeklyHistory(uuid4()), WeeklyHistory(CAMPAIGN, 2)]
)
def test_mixed_or_ahead_history_is_rejected(history):
    with pytest.raises(ValueError):
        select_weekly(observation(item(1)), history)


@pytest.mark.parametrize(
    "bad_observation,bad_history",
    [(None, WeeklyHistory(CAMPAIGN)), (observation(), {})],
)
def test_untyped_selection_is_rejected(bad_observation, bad_history):
    with pytest.raises(TypeError):
        select_weekly(bad_observation, bad_history)


def test_decode_copies_private_mutable_projection():
    source = document()
    result = decode_observation(source)
    source["items"][1][-1] = "MUTATED"
    assert result.items[1].value.text == "Private request"
    assert result.items[0].value.disposition == "withdrawn"
    assert not hasattr(result.items[0].value, "text")
    assert decode_observation(observation_document(result)) == result


def test_observation_serialization_rejects_untyped_inputs():
    with pytest.raises(TypeError):
        observation_document(document())


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


@pytest.mark.parametrize("campaign", [str(CAMPAIGN), None, True])
def test_capture_rejects_noncanonical_campaign_before_database_io(campaign):
    with pytest.raises(ValueError, match="canonical campaign"):
        capture_weekly_observation(campaign)
