"""Financial source scope/coverage cannot manufacture zero or cross households."""

from copy import deepcopy
from dataclasses import replace
from datetime import date
from uuid import uuid4

import pytest

from parishkit.stewardship.responses.financial_inputs import (
    InvalidFinancialSource,
    financial_definition,
    financial_inputs,
    giving_observation,
)

from .financial_factory import CAMPAIGN, configuration, cursor, record


def definition():
    """Build through actual campaign validation, not a guessed financial scope."""
    return financial_definition(configuration(), campaign_id=CAMPAIGN)


def test_definition_keeps_explicit_periods_funds_and_noncalendar_year_label():
    """A fiscal-year period stays intact rather than being relabeled calendar-year."""
    values = configuration()
    values["financial"].update(start="2027-07-01", end="2028-06-30")
    result = financial_definition(values, campaign_id=CAMPAIGN)
    assert result.year_label == "2027–2028"
    assert result.upcoming.start == date(2027, 7, 1)
    assert result.comparison.funds == (9,)
    assert result.upcoming.funds == (4,)
    values["year_label"] = "Configured stewardship year"
    assert (
        financial_definition(values, campaign_id=CAMPAIGN).year_label
        == values["year_label"]
    )


@pytest.mark.parametrize(
    "change", [{"financial": None}, {"modules": []}, {"financial": {}}]
)
def test_missing_financial_definition_is_not_a_usable_form(change):
    """Absent dates cannot produce a misleading upcoming-period pledge form."""
    with pytest.raises(InvalidFinancialSource, match="definition is unavailable"):
        financial_definition(configuration() | change, campaign_id=CAMPAIGN)


def test_delta_keeps_actual_older_giving_as_of_instead_of_newer_watermark():
    """Family census deltas must not suggest that financial data was refreshed."""
    scope = definition()
    observation = giving_observation(cursor(scope), scope)
    assert observation.observed_at.isoformat() == "2026-10-15T04:00:00+00:00"
    assert observation.through_date == date(2026, 10, 15)
    value = cursor(scope)
    value["full_started_at"] = "2026-10-16T02:00:00+00:00"
    assert giving_observation(value, scope).through_date == date(2026, 10, 15)


@pytest.mark.parametrize(
    "path,value",
    [
        (("schema",), "unknown"),
        (("window_digest",), "0" * 64),
        (("full_snapshot_id",), "not-a-uuid"),
        (("full_started_at",), "2026-10-17T04:00:00+00:00"),
        (("full_started_at",), "2026-10-15T04:00:00"),
        (("load",), None),
        (("load", "schema"), "unrecognized"),
        (("load", "window_digest"), "0" * 64),
        (("load", "giving_as_of_date"), "2026-10-17"),
        (("load", "giving_as_of_date"), "2026-09-01"),
        (("load", "giving_as_of_date"), None),
        (("load", "as_of_date"), "2026-10-14"),
        (("load", "as_of_date"), "2026-11-01"),
    ],
)
def test_incomplete_or_inconsistent_coverage_is_unavailable(path, value):
    """A promoted flag or matching record count alone cannot prove scoped money."""
    scope = definition()
    data = cursor(scope)
    target = data if len(path) == 1 else data[path[0]]
    target[path[-1]] = value
    assert giving_observation(data, scope) is None


def test_missing_cursor_or_funds_or_other_campaign_is_not_verified_zero():
    """No current-window proof means unavailable even if no records were returned."""
    scope = definition()
    for data in ({}, None):
        assert giving_observation(data, scope) is None
    another = financial_definition(configuration(), campaign_id=uuid4())
    assert giving_observation(cursor(scope), another) is None
    values = configuration()
    values["financial"]["comparison_fund_duids"] = []
    empty = financial_definition(values, campaign_id=CAMPAIGN)
    assert giving_observation(cursor(empty), empty) is None
    result = financial_inputs(scope, None, family_duid=1, pledges=[], contributions=[])
    assert result.pledge.canonical is None and result.contributions.canonical is None


def test_complete_scoped_totals_preserve_signed_adjustments_and_date_cutoffs():
    """Only mapped comparison money through the actual giving observation counts."""
    scope = definition()
    observation = giving_observation(cursor(scope), scope)
    result = financial_inputs(
        scope,
        observation,
        family_duid=1,
        pledges=[
            record("1200.00"),
            record("-100.00"),
            record("999.00", fund_key="4"),
            record("800.00", effective_date="2027-01-01"),
        ],
        contributions=[
            record("10.00"),
            record("-0.01"),
            record("99.00", effective_date="2026-10-16"),
        ],
    )
    assert result.pledge.canonical == "1100.00"
    assert result.contributions.canonical == "9.99"
    zero = financial_inputs(
        scope, observation, family_duid=1, pledges=[], contributions=[]
    )
    assert zero.pledge.canonical == zero.contributions.canonical == "0.00"
    assert result.comparison() != zero.comparison()
    assert result.comparison() != replace(result, observation=None).comparison()


@pytest.mark.parametrize(
    "changes",
    [
        {"family_key": "2"},
        {"family_key": "2", "effective_date": "2027-01-01"},
        {"fund_key": "09"},
        {"fund_key": "٩"},
        {"amount": "1.001"},
        {"amount": "private-invalid-value"},
        {"effective_date": "not-a-date"},
        {"extra": "private-value"},
    ],
)
def test_malformed_or_foreign_retained_records_never_become_family_totals(changes):
    """Even an out-of-period foreign record indicates a broken ownership adapter."""
    scope = definition()
    with pytest.raises(InvalidFinancialSource) as failure:
        financial_inputs(
            scope,
            giving_observation(cursor(scope), scope),
            family_duid=1,
            pledges=[record(**changes)],
            contributions=[],
        )
    assert str(failure.value) == "The Family financial source is unavailable."


def test_projection_includes_financial_definition_not_unrelated_metadata():
    """Baseline comparisons cover options and periods, not arbitrary cursor fields."""
    scope = definition()
    data = cursor(scope)
    noisy = deepcopy(data)
    noisy["unrelated"] = "metadata"
    assert giving_observation(noisy, scope) == giving_observation(data, scope)
    values = configuration()
    values["year_label"] = "Changed display year"
    changed = financial_definition(values, campaign_id=CAMPAIGN)
    before = financial_inputs(scope, None, family_duid=1, pledges=[], contributions=[])
    after = financial_inputs(changed, None, family_duid=1, pledges=[], contributions=[])
    assert before.comparison() != after.comparison()
