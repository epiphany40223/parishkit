"""Financial source scope/coverage cannot manufacture zero or cross households."""

from copy import deepcopy
from dataclasses import replace
from datetime import date, timedelta
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
    """A fiscal-year period stays intact rather than being relabeled calendar-year.

    The pledge is labeled by its period even when the campaign's Stewardship
    year label says otherwise (#1923), so it agrees with the start date.
    """
    values = configuration()
    values["financial"].update(start="2055-07-01", end="2056-06-30")
    result = financial_definition(values, campaign_id=CAMPAIGN)
    assert result.year_label == "2055–2056"
    assert result.upcoming.start == date(2055, 7, 1)
    assert result.comparison.funds == (9,)
    assert result.upcoming.funds == (4,)
    values["year_label"] = "Configured stewardship year"
    assert financial_definition(values, campaign_id=CAMPAIGN).year_label == "2055–2056"


def test_pledge_year_is_the_period_start_year_not_the_year_label():
    """A 2026 label on a 2027 period shows "Your 2027 pledge" (#1923)."""
    values = configuration()
    values["financial"].update(start="2027-01-01", end="2027-12-31")
    values["year_label"] = "2026"
    result = financial_definition(values, campaign_id=CAMPAIGN)
    assert result.year_label == "2027"
    assert result.upcoming.start.year == 2027
    # The campaign year for page and email text still follows the label.
    assert result.campaign_year == "2026"


def test_definition_leaves_out_short_labels():
    """A short label (#1569) is Admin wording, never in the Family's definition."""
    option = {"id": str(uuid4()), "label": "Online giving", "free_text": False}
    values = configuration() | {"share_options": [option]}
    plain = financial_definition(values, campaign_id=CAMPAIGN)
    assert len(plain.options) == 1
    values["share_options"] = [option | {"short_label": "Online"}]
    assert financial_definition(values, campaign_id=CAMPAIGN) == plain


@pytest.mark.parametrize(
    "options", [["not an option"], [{"id": "x", "label": "y", "unknown": 1}]]
)
def test_malformed_share_options_are_not_a_usable_form(options):
    """An option that is not an object, or has an unknown key, still refuses."""
    values = configuration() | {"share_options": options}
    with pytest.raises(InvalidFinancialSource, match="definition is unavailable"):
        financial_definition(values, campaign_id=CAMPAIGN)


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


def test_same_totals_with_newer_observation_do_not_require_review():
    """Only observation context changed; the reviewed snapshot retains its as-of."""
    scope = definition()
    observation = giving_observation(cursor(scope), scope)
    inputs = financial_inputs(
        scope, observation, family_duid=1, pledges=[], contributions=[]
    )
    newer = replace(
        observation,
        observed_at=observation.observed_at + timedelta(days=1),
        through_date=observation.through_date + timedelta(days=1),
    )
    assert inputs.comparison() == replace(inputs, observation=newer).comparison()


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
        {"schema_version": True},
        {"schema_version": 2},
        {"schema_version": None},
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


def test_actual_giving_normalization_matches_form_totals(tmp_path):
    """Use the real provider decoder/normalizer, not only hand-built records."""
    from .test_source_giving import contribution, pledge, read

    loaded, _ = read(tmp_path, [pledge()], [contribution()])
    assert loaded.pledges["1"] == record("1200.00", effective_date="2026-01-01")
    scope = definition()
    result = financial_inputs(
        scope,
        giving_observation(cursor(scope), scope),
        family_duid=1,
        pledges=loaded.pledges.values(),
        contributions=loaded.contributions.values(),
    )
    assert result.pledge.canonical == "1200.00"
    assert result.contributions.canonical == "100.25"
    malformed = record()
    del malformed["schema_version"]
    with pytest.raises(InvalidFinancialSource):
        financial_inputs(
            scope,
            giving_observation(cursor(scope), scope),
            family_duid=1,
            pledges=[malformed],
            contributions=[],
        )


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


@pytest.mark.parametrize(
    ("start", "end", "label"),
    [
        (date(2027, 1, 1), date(2027, 12, 31), "2027"),
        (date(2027, 7, 1), date(2028, 6, 30), "2027–2028"),
    ],
)
def test_period_years_names_a_period_by_its_years(start, end, label):
    """The one pledge-year rule: one year, or both years of a spanning period."""
    from parishkit.stewardship.web.presentation import period_years

    assert period_years(start, end) == label
