"""DOM-01 serialization and validation contracts without a database or clock."""

import json
from dataclasses import FrozenInstanceError
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, localcontext
from zoneinfo import ZoneInfo

import pytest

from parishkit.stewardship.campaigns.domain import (
    CampaignModule,
    CampaignState,
    EnabledModules,
    LocalDayInterval,
    Money,
    Percentage,
    PortalRole,
    SystemMode,
    UTCInterval,
    ValueOrigin,
)

ENUM_VALUES = [
    (
        CampaignState,
        [
            "draft",
            "scheduled",
            "active",
            "closed",
            "archived",
            "purging",
            "purge_cleanup_failed",
            "purged",
        ],
    ),
    (SystemMode, ["testing", "production"]),
    (CampaignModule, ["census", "ministry", "financial"]),
    (PortalRole, ["administrator", "staff", "ministry_leader"]),
    (ValueOrigin, ["baseline", "current", "family_submitted", "proposed"]),
]


@pytest.mark.parametrize(("enum", "expected"), ENUM_VALUES)
def test_enum_wire_values_are_stable(enum, expected):
    """Renaming a persisted state requires an intentional contract migration."""
    assert [item.value for item in enum] == expected
    assert json.loads(json.dumps(list(enum))) == expected
    assert [enum(value) for value in expected] == list(enum)
    for value in ("unknown", "", None, 1, True, expected[0].upper()):
        with pytest.raises(ValueError):
            enum(value)


def test_module_serialization():
    """Selection is nonempty, immutable, and independent of insertion order."""
    selection = EnabledModules.from_list(["ministry", "census"])
    assert selection.to_list() == ["census", "ministry"]
    assert selection == EnabledModules.from_list(selection.to_list())
    with pytest.raises(FrozenInstanceError):
        selection.values = frozenset()


@pytest.mark.parametrize(
    "values", [[], ["unknown"], ["census", "census"], [True], "census", [None]]
)
def test_invalid_module_serialization(values):
    """No empty, unknown, repeated, or mistyped module selection is accepted."""
    with pytest.raises(ValueError):
        EnabledModules.from_list(values)


@pytest.mark.parametrize(
    "values", [set(), {CampaignModule.CENSUS}, frozenset({"census"})]
)
def test_module_constructor_is_also_validated(values):
    """Internal callers cannot bypass immutability or enum validation."""
    with pytest.raises(ValueError):
        EnabledModules(values)


@pytest.mark.parametrize(
    ("text", "cents", "canonical"),
    [
        ("0", 0, "0.00"),
        ("-0", 0, "0.00"),
        ("1234.56", 123456, "1234.56"),
        ("1.2", 120, "1.20"),
        ("-1.23", -123, "-1.23"),
        ("999999999.99", 99_999_999_999, "999999999.99"),
        ("-999999999.99", -99_999_999_999, "-999999999.99"),
    ],
)
def test_money_round_trip(text, cents, canonical):
    """Exact cents round trip without binary floats or implicit truncation."""
    money = Money.from_string(text)
    assert money.cents == cents
    assert money.to_string() == canonical
    assert Money.from_string(json.loads(json.dumps(money.to_string()))) == money


@pytest.mark.parametrize(
    "text",
    [
        "NaN",
        "sNaN",
        "Infinity",
        "-Infinity",
        "1.001",
        "1.000000000000000000000000000000000001",
        "1000000000",
        "-1000000000",
        "",
        "not money",
        1.2,
        True,
    ],
)
def test_invalid_money_serialization(text):
    """Nonfinite, oversized, fractional-cent, and non-string values are rejected."""
    with pytest.raises(ValueError):
        Money.from_string(text)


@pytest.mark.parametrize("cents", [True, 1.2, "100", 100_000_000_000, -100_000_000_000])
def test_invalid_cent_values(cents):
    """Direct construction enforces the same reporting bounds and exact type."""
    with pytest.raises(ValueError):
        Money(cents)


def test_percentage_keeps_exact_inputs():
    """Undefined ratios remain distinct from zero; stored inputs are lossless."""
    assert Percentage(0, 0).value is None
    assert Percentage(4, 0).value is None
    assert Percentage(0, 7).value == Decimal(0)
    assert Percentage(3, 4).value == Decimal(75)
    value = Percentage(1, 3)
    assert value.value == Decimal("33.33333333333333333333333333")
    assert Percentage(**json.loads(json.dumps(value.to_dict()))) == value


@pytest.mark.parametrize(
    ("numerator", "denominator"),
    [(-1, 2), (1, -1), (True, 1), (1, False), (1.5, 2), (1, "2")],
)
def test_percentage_rejects_invalid_counts(numerator, denominator):
    """Fractions, booleans, and negative values are not count inputs."""
    with pytest.raises(ValueError):
        Percentage(numerator, denominator)


def test_decimal_context_does_not_change_values():
    """A third-party library's global Decimal precision cannot round our money."""
    with localcontext(prec=2):
        assert Money.from_string("1234.56").cents == 123456
        assert Percentage(1, 3).value == Decimal("33.33333333333333333333333333")
        with pytest.raises(ValueError):
            Money.from_string("999999999.991")


def test_utc_interval_round_trip_and_boundaries():
    """Aware offsets normalize to UTC and the closing instant is excluded."""
    start = datetime(2026, 9, 7, tzinfo=ZoneInfo("America/New_York"))
    interval = UTCInterval(start, start + timedelta(days=1))
    assert interval.start == datetime(2026, 9, 7, 4, tzinfo=UTC)
    assert interval.start.tzinfo is UTC and interval.end.tzinfo is UTC
    assert interval.contains(start)
    assert interval.contains(interval.end - timedelta(microseconds=1))
    assert not interval.contains(interval.end)
    assert not interval.contains(interval.start - timedelta(microseconds=1))
    raw = json.loads(json.dumps(interval.to_dict()))
    assert (
        UTCInterval(
            **{key: datetime.fromisoformat(value) for key, value in raw.items()}
        )
        == interval
    )


def test_empty_interval_contains_nothing():
    """A skipped civil day has an empty interval, not a fabricated 24-hour day."""
    instant = datetime(2011, 12, 30, 10, tzinfo=UTC)
    interval = UTCInterval(instant, instant)
    assert not interval.contains(instant)


def test_interval_rejects_naive_or_backwards_input():
    """Never interpret naive timestamps in the host machine's timezone."""
    instant = datetime(2026, 9, 7, tzinfo=UTC)
    for start, end in [
        (instant.replace(tzinfo=None), instant),
        (instant, None),
        (instant, instant - timedelta(seconds=1)),
    ]:
        with pytest.raises(ValueError):
            UTCInterval(start, end)
    with pytest.raises(ValueError):
        UTCInterval(instant, instant).contains(instant.replace(tzinfo=None))


def test_local_day_serialization_retains_zone():
    """Persist the campaign timezone snapshot as well as the resolved interval."""
    interval = UTCInterval(
        datetime(2026, 9, 7, tzinfo=UTC), datetime(2026, 9, 8, tzinfo=UTC)
    )
    day = LocalDayInterval(date(2026, 9, 7), "UTC", interval)
    assert day.to_dict() == {
        "day": "2026-09-07",
        "timezone": "UTC",
        "interval": interval.to_dict(),
    }
    with pytest.raises(ValueError):
        LocalDayInterval(interval.start, "UTC", interval)
    with pytest.raises(ValueError):
        LocalDayInterval(day.day, "UTC", "not an interval")
    with pytest.raises(ValueError):
        LocalDayInterval(day.day, None, interval)
    for invalid_zone in ("Not/A_Zone", "", "../private"):
        with pytest.raises(ValueError, match="timezone must be an IANA identifier"):
            LocalDayInterval(day.day, invalid_zone, interval)
