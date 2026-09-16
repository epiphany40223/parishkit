"""Exact-cutoff population, response and money calculations without a database."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, localcontext
from uuid import UUID

import pytest

from parishkit.stewardship.reports.calculations import (
    Calculation,
    CohortFamily,
    LiveResponse,
    Promotion,
    calculate_participation,
)
from parishkit.stewardship.reports.money import MoneyAmount

DAY = date(2026, 10, 1)
START = datetime(2026, 10, 1, 4, tzinfo=UTC)
FIRST, SECOND = UUID(int=1), UUID(int=2)


def calculation(**changes):
    """Two historical Families, with only the first still eligible at the cutoff."""
    first = Promotion(1, START - timedelta(days=1))
    second = Promotion(2, START + timedelta(days=1, hours=1))
    return replace(
        Calculation(
            "historical",
            DAY,
            DAY + timedelta(days=2),
            DAY + timedelta(days=2),
            "America/New_York",
            True,
            second,
            3,
            (first, second),
            (
                CohortFamily(FIRST, first.promoted_at, 1),
                CohortFamily(SECOND, first.promoted_at, 1),
            ),
            frozenset({FIRST}),
            (
                LiveResponse(FIRST, 1, START + timedelta(hours=1), MoneyAmount(10001)),
                LiveResponse(SECOND, 2, START + timedelta(hours=2), MoneyAmount(20002)),
                LiveResponse(
                    FIRST, 3, START + timedelta(days=1, hours=2), MoneyAmount(30003)
                ),
            ),
        ),
        **changes,
    )


def test_historical_repeat_submission_changes_pledge_not_first_response():
    days = calculate_participation(calculation())
    assert [day.first_responses for day in days] == [2, 0, 0]
    assert [day.cumulative_responses for day in days] == [2, 2, 2]
    assert [day.cohort_denominator for day in days] == [2, 2, 2]
    assert [day.pledge_total for day in days] == [
        Decimal("300.03"),
        Decimal("500.05"),
        Decimal("500.05"),
    ]
    assert [day.source_generation for day in days] == [1, 2, 2]


def test_current_scope_excludes_inactive_family_from_every_series():
    days = calculate_participation(calculation(population_scope="current"))
    assert [day.first_responses for day in days] == [1, 0, 0]
    assert [day.cumulative_responses for day in days] == [1, 1, 1]
    assert [day.cohort_denominator for day in days] == [1, 1, 1]
    assert [day.pledge_total for day in days] == [
        Decimal("100.01"),
        Decimal("300.03"),
        Decimal("300.03"),
    ]
    assert {day.source_generation for day in days} == {2}


def test_no_early_source_is_unavailable_not_a_later_population():
    context = calculation()
    context = replace(context, promotions=(context.source,))
    days = calculate_participation(context)
    assert not days[0].population_available
    assert days[0].participation == "Unavailable"
    assert days[0].pledge_total is None
    assert days[1].population_available


@pytest.mark.parametrize(
    "late_field", ["first_eligible_at", "first_eligible_generation"]
)
def test_historical_cohort_requires_both_time_and_generation(late_field):
    context = calculation(responses=())
    second = replace(
        context.families[1],
        **{late_field: context.source.promoted_at if late_field.endswith("at") else 2},
    )
    days = calculate_participation(
        replace(context, families=(context.families[0], second))
    )
    assert [day.cohort_denominator for day in days] == [1, 2, 2]


def test_future_provenance_does_not_enter_a_frozen_historical_cohort():
    context = calculation(responses=())
    later = CohortFamily(UUID(int=3), START, 3)
    days = calculate_participation(
        replace(context, families=context.families + (later,))
    )
    assert {day.cohort_denominator for day in days} == {2}


def test_exact_midnight_belongs_only_to_the_next_day():
    response = LiveResponse(FIRST, 1, START + timedelta(days=1), MoneyAmount(0))
    days = calculate_participation(calculation(responses=(response,)))
    assert [day.first_responses for day in days] == [0, 1, 0]
    assert [day.cumulative_responses for day in days] == [0, 1, 1]


@pytest.mark.parametrize(
    "day,utc_end",
    [
        (date(2026, 3, 8), datetime(2026, 3, 9, 4, tzinfo=UTC)),
        (date(2026, 11, 1), datetime(2026, 11, 2, 5, tzinfo=UTC)),
    ],
)
def test_dst_day_end_uses_campaign_zone(day, utc_end):
    source = Promotion(1, utc_end - timedelta(days=5))
    context = calculation(
        start_date=day,
        end_date=day + timedelta(days=1),
        through_date=day,
        source=source,
        promotions=(source,),
        families=(CohortFamily(FIRST, source.promoted_at, 1),),
        responses=(
            LiveResponse(
                FIRST, 1, utc_end - timedelta(microseconds=1), MoneyAmount(100)
            ),
            LiveResponse(FIRST, 2, utc_end, MoneyAmount(200)),
        ),
    )
    (result,) = calculate_participation(context)
    assert result.first_responses == 1
    assert result.pledge_total == Decimal("1.00")


def test_skipped_calendar_date_has_no_daily_events():
    day = date(2011, 12, 30)
    source = Promotion(1, datetime(2011, 12, 28, tzinfo=UTC))
    context = calculation(
        start_date=day,
        end_date=day + timedelta(days=1),
        through_date=day,
        timezone="Pacific/Apia",
        source=source,
        promotions=(source,),
        families=(CohortFamily(FIRST, source.promoted_at, 1),),
        responses=(LiveResponse(FIRST, 1, source.promoted_at, MoneyAmount(100)),),
    )
    (result,) = calculate_participation(context)
    assert result.first_responses == 0
    assert result.cumulative_responses == 1


def test_missing_effective_pledge_is_not_zero_and_can_be_completed_later():
    context = calculation()
    responses = tuple(
        replace(row, pledge=MoneyAmount(None)) if row.sequence == 1 else row
        for row in context.responses
    )
    days = calculate_participation(replace(context, responses=responses))
    assert not days[0].pledge_available and days[0].pledge_total is None
    assert days[1].pledge_total == Decimal("500.05")
    assert all(
        not day.pledge_available
        for day in calculate_participation(replace(context, financial_enabled=False))
    )


def test_empty_known_population_is_distinct_from_unavailable():
    days = calculate_participation(
        calculation(population_scope="current", current_family_ids=frozenset())
    )
    assert days[0].population_available
    assert days[0].cohort_denominator == 0
    assert days[0].pledge_total == Decimal("0.00")
    assert days[0].participation == "0 out of 0 (—)"


def test_exact_integer_money_ignores_decimal_context_precision():
    with localcontext() as context:
        context.prec = 2
        assert calculate_participation(calculation())[0].pledge_total == Decimal(
            "300.03"
        )


def test_input_order_never_changes_the_calculation():
    context = calculation()
    assert calculate_participation(
        replace(
            context,
            responses=context.responses[::-1],
            promotions=context.promotions[::-1],
            families=context.families[::-1],
        )
    ) == calculate_participation(context)


def test_pre_start_is_empty_and_end_caps_the_series():
    assert (
        calculate_participation(calculation(through_date=DAY - timedelta(days=1))) == ()
    )
    assert (
        len(calculate_participation(calculation(through_date=DAY + timedelta(days=99))))
        == 3
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"population_scope": "unknown"},
        {"financial_enabled": 1},
        {"submission_watermark": True},
        {"submission_watermark": 1},
        {"promotions": ()},
        {"families": ()},
        {"responses": []},
        {"current_family_ids": set()},
        {"current_family_ids": frozenset({UUID(int=99)})},
        {"timezone": "Not/AZone"},
        {"start_date": datetime(2026, 10, 1)},
    ],
)
def test_reject_ambiguous_or_mixed_calculation_inputs(changes):
    with pytest.raises(ValueError):
        calculation(**changes)


@pytest.mark.parametrize("field", ["promotions", "families", "responses"])
def test_reject_duplicate_input_identity(field):
    context = calculation()
    with pytest.raises(ValueError, match="repeat"):
        replace(context, **{field: getattr(context, field) * 2})


def test_reject_naive_provenance_and_invalid_identity():
    with pytest.raises(ValueError):
        Promotion(True, START)
    with pytest.raises(ValueError):
        Promotion(1, START.replace(tzinfo=None))
    with pytest.raises(ValueError):
        CohortFamily("family", START, 1)
    with pytest.raises(ValueError):
        LiveResponse(FIRST, 1, START, MoneyAmount(-1))
    with pytest.raises(ValueError):
        LiveResponse(FIRST, 1, START, 100)
    with pytest.raises(TypeError):
        calculate_participation(None)
