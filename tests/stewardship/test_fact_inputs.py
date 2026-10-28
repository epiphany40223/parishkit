"""Pure exact-input validation, including monetary precision and local dates."""

from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from parishkit.stewardship.reports.inputs import (
    FactInputs,
    expected_dates,
    report_end,
    validate_day,
)


def inputs():
    """A valid frozen identity independent of database contents."""
    return FactInputs(
        uuid4(),
        "historical",
        uuid4(),
        0,
        uuid4(),
        date(2026, 10, 2),
        review_watermark=0,
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("campaign_id", "bad"),
        ("source_id", None),
        ("timezone_configuration_id", 1),
        ("population_scope", "all"),
        ("submission_watermark", True),
        ("submission_watermark", -1),
        ("submission_watermark", 2**63),
        ("review_watermark", True),
        ("review_watermark", -1),
        ("review_watermark", 2**63),
        ("review_watermark", None),
        ("through_date", datetime(2026, 10, 2, tzinfo=UTC)),
    ],
)
def test_fact_inputs_reject_ambiguous_or_coerced_values(field, value):
    """Exact storage identities never silently change meaning during coercion."""
    with pytest.raises(ValueError):
        replace(inputs(), **{field: value})


def test_fact_inputs_require_the_review_watermark_by_keyword():
    """No caller can leave the pledge review watermark out (#2202)."""
    with pytest.raises(TypeError):
        FactInputs(uuid4(), "historical", uuid4(), 0, uuid4(), date(2026, 10, 2))
    with pytest.raises(TypeError):
        FactInputs(uuid4(), "historical", uuid4(), 0, uuid4(), date(2026, 10, 2), 0)


def test_dates_are_inclusive_bounded_and_include_leap_days():
    """Dates remain dates instead of fixed-duration UTC timestamp arithmetic.

    The through-date ends the range, past the end date too (#2298): its
    callers cap it at the report end.
    """
    assert expected_dates(date(2024, 2, 28), date(2024, 3, 1), date(2024, 3, 1)) == (
        date(2024, 2, 28),
        date(2024, 2, 29),
        date(2024, 3, 1),
    )
    assert expected_dates(date(2024, 2, 28), date(2024, 3, 1), date(2024, 3, 3)) == (
        date(2024, 2, 28),
        date(2024, 2, 29),
        date(2024, 3, 1),
        date(2024, 3, 2),
        date(2024, 3, 3),
    )
    assert expected_dates(date(2024, 2, 28), date(2024, 3, 1), date(2024, 2, 27)) == ()
    with pytest.raises(ValueError):
        expected_dates(date(2024, 3, 2), date(2024, 3, 1), date(2024, 3, 3))


def test_report_end_is_the_later_of_the_end_date_and_the_newest_response():
    """The report end moves past the end date only to a late response's day.

    The response's instant is read in the campaign's zone: 03:30 UTC on
    November 3 is still November 2 in New York (#2298).
    """
    end = date(2026, 11, 1)
    zone = "America/New_York"
    assert report_end(end) == end
    assert report_end(end, datetime(2026, 10, 20, 15, tzinfo=UTC), zone) == end
    assert report_end(end, datetime(2026, 11, 2, 0, 30, tzinfo=UTC), zone) == end
    assert report_end(end, datetime(2026, 11, 3, 3, 30, tzinfo=UTC), zone) == date(
        2026, 11, 2
    )


def day():
    """An exact monetary fact row with all availability metadata present."""
    return dict(
        local_date=date(2026, 10, 1),
        first_responses=1,
        cumulative_responses=1,
        cohort_denominator=10,
        source_generation=1,
        source_as_of=datetime(2026, 10, 1, tzinfo=UTC),
        population_available=True,
        pledge_available=True,
        pledge_total=Decimal("1234.56"),
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("first_responses", True),
        ("cohort_denominator", -1),
        ("cumulative_responses", 2**63),
        ("local_date", "2026-10-01"),
        ("population_available", 1),
        ("pledge_available", "yes"),
        ("source_generation", True),
        ("source_generation", 0),
        ("source_as_of", datetime(2026, 10, 1)),
        ("source_as_of", "2026-10-01"),
        ("pledge_total", 1.01),
        ("pledge_total", "1.01"),
        ("pledge_total", Decimal("NaN")),
        ("pledge_total", Decimal("Infinity")),
        ("pledge_total", Decimal("0.001")),
        ("pledge_total", Decimal("-1")),
        ("pledge_total", Decimal("10000000000000000.00")),
    ],
)
def test_day_rejects_imprecise_types_and_monetary_values(field, value):
    """No float-to-decimal conversion or implicit field rounding reaches storage."""
    with pytest.raises(ValueError):
        validate_day(day() | {field: value})


def test_day_requires_full_schema_and_accepts_exact_valid_values():
    """Cross-record/availability consistency remains SQL's separate responsibility."""
    validate_day(day())
    validate_day(
        day() | {"source_generation": None, "source_as_of": None, "pledge_total": None}
    )
    with pytest.raises(ValueError):
        validate_day({})
