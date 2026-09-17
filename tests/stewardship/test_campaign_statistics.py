"""Exact current statistics, independent populations and detached-input parity."""

from copy import deepcopy
from datetime import UTC, datetime
from uuid import UUID

import pytest

from parishkit.stewardship.reports.statistics import (
    StatisticsInputs,
    StatisticsUnavailable,
    calculate_statistics,
)
from parishkit.stewardship.responses.financial_inputs import financial_definition

from .financial_factory import CAMPAIGN, configuration, cursor, record


def observation(*, count=3, financial=True):
    """A minimal normalized synthetic parish; no private household fields needed."""
    values = configuration()
    definition = financial_definition(values, campaign_id=CAMPAIGN)
    if not financial:
        values.update(modules=["census"], financial=None)
    families, members, contacts = {}, {}, {}
    for index in range(1, count + 1):
        key = str(index)
        families[key] = dict(
            schema_version=1,
            active=True,
            parishioner=True,
            portal_eligible=True,
            email_eligible=True,
            active_head_duids=[index],
        )
        members[key] = dict(schema_version=1, family_key=key, active=True)
        contacts[f"member:{key}"] = dict(
            schema_version=1,
            owner_kind="member",
            owner_key=key,
            emails=[dict(valid=True, value=f"head{index}@example.org")],
        )
    return dict(
        schema="campaign-statistics-v1",
        campaign_id=str(CAMPAIGN),
        configuration_id="33333333-3333-4333-8333-333333333333",
        observed_at="2026-10-16T04:01:00+00:00",
        submission_watermark=4,
        configuration=values,
        source=dict(
            id="44444444-4444-4444-8444-444444444444",
            generation=2,
            promoted_at="2026-10-16T04:00:30+00:00",
            counts={"family": count, "member": count, "pledge": 0},
            pledge_count=0,
            cursor=cursor(definition),
        ),
        corpus=dict(family=families, member=members, contact=contacts),
        ever_eligible=list(range(1, count + 1)),
        responses=[],
        refusals=[],
        pledges=[],
    )


def calculate(document, **options):
    """Exercise the same canonical detachment used by the SQL capture owner."""
    return calculate_statistics(StatisticsInputs.capture(document), **options)


def test_active_statistics_and_exact_available_zero():
    result = calculate(observation())
    assert result.active.families == result.active.active_members == 3
    assert result.active.eligible_email == result.active.deliverable_email == 3
    assert result.active.responses == 0
    assert result.active.annual_pledge.canonical == "0.00"
    assert result.active.comparison_pledge.canonical == "0.00"
    assert result.active.no_deliverable_email == 0
    assert result.inactive is None
    assert result.active.proportion("responses") == "0 out of 3 (0%)"
    assert result.giving.observed_at == datetime(2026, 10, 15, 4, tzinfo=UTC)
    assert result.giving.observed_at < result.source_as_of < result.observed_at


def test_eligible_and_deliverable_are_separate_family_scoped_populations():
    document = observation()
    document["corpus"]["contact"]["member:2"]["emails"] = [
        dict(valid=True, value="head1@example.org")
    ]
    document["refusals"] = [[1, "head1@example.org"]]
    result = calculate(document).active
    assert result.eligible_email == 3 and result.deliverable_email == 2
    assert result.no_deliverable_email == 1
    assert result.proportion("deliverable_email") == "2 out of 3 (66.7%)"


def test_one_unsuppressed_address_suffices_and_duplicates_do_not_add_families():
    document = observation(count=1)
    emails = document["corpus"]["contact"]["member:1"]["emails"]
    emails.extend([dict(valid=True, value="other@example.org"), emails[0]])
    document["refusals"] = [[1, "head1@example.org"]]
    result = calculate(document).active
    assert result.eligible_email == result.deliverable_email == 1


def test_absent_invalid_and_nonhead_email_never_add_eligible_families():
    document = observation(count=2)
    document["corpus"]["family"]["1"].update(email_eligible=False)
    document["corpus"]["contact"]["member:1"]["emails"] = [
        dict(valid=False, value="not an email")
    ]
    document["corpus"]["family"]["2"].update(email_eligible=False, active_head_duids=[])
    result = calculate(document).active
    assert result.families == result.no_deliverable_email == 2
    assert result.eligible_email == result.deliverable_email == 0


@pytest.mark.parametrize("reason", ["inactive", "non_parishioner", "removed"])
def test_formerly_eligible_subtotal_never_changes_active_denominator(reason):
    document = observation()
    document["responses"] = [[1, 1, "10.01"], [2, 4, "20.02"]]
    family = document["corpus"]["family"]["2"]
    family.update(portal_eligible=False, email_eligible=False)
    if reason == "inactive":
        family.update(active=False)
    elif reason == "non_parishioner":
        family.update(parishioner=False)
    else:
        del document["corpus"]["family"]["2"]
        del document["corpus"]["member"]["2"]
        document["source"]["counts"].update(family=2, member=2)
    ordinary = calculate(document)
    expanded = calculate(document, include_inactive=True)
    assert expanded.active == ordinary.active
    assert expanded.active.families == 2 and expanded.active.responses == 1
    assert expanded.active.annual_pledge.canonical == "10.01"
    assert expanded.inactive.families == expanded.inactive.responses == 1
    assert expanded.inactive.annual_pledge.canonical == "20.02"
    assert expanded.inactive.comparison_pledge.available is (reason != "removed")


def test_never_eligible_nonparishioner_does_not_enter_either_population():
    document = observation()
    document["ever_eligible"].remove(3)
    document["corpus"]["family"]["3"].update(
        parishioner=False, portal_eligible=False, email_eligible=False
    )
    result = calculate(document, include_inactive=True)
    assert result.active.families == result.active.active_members == 2
    assert result.inactive.families == 0


def test_no_source_and_observed_empty_are_different():
    empty = observation(count=0)
    available = calculate(empty)
    assert available.active.families == 0
    assert available.active.proportion("responses") == "0 out of 0 (—)"
    empty["source"] = None
    missing = calculate(empty, include_inactive=True)
    assert missing.active is missing.inactive is missing.source_id is None


@pytest.mark.parametrize("financial,covered", [(False, True), (True, False)])
def test_disabled_financial_or_incomplete_source_is_not_observed_zero(
    financial, covered
):
    document = observation(financial=financial)
    if not covered:
        document["source"]["cursor"] = {}
    result = calculate(document)
    assert result.financial_enabled is financial
    assert result.active.annual_pledge.available is financial
    assert not result.active.comparison_pledge.available
    assert result.giving is None


def test_unfinished_draft_financial_mapping_does_not_hide_known_population():
    document = observation()
    document["configuration"]["financial"] = None
    result = calculate(document)
    assert result.financial_enabled and result.financial is None
    assert result.active.families == 3
    assert not result.active.annual_pledge.available
    assert not result.active.comparison_pledge.available


def test_comparison_uses_exact_mapped_funds_period_and_signed_cents():
    document = observation(count=1)
    document["pledges"] = [
        record("1200.01"),
        record("-0.01"),
        record("9999.00", fund_key="4"),
        record("9999.00", effective_date="2025-10-01"),
    ]
    # The fifth source pledge belongs to an unrelated household. Its count is
    # retained as coverage evidence, but its private value is not detached.
    document["source"]["counts"]["pledge"] = 5
    document["source"]["pledge_count"] = 5
    result = calculate(document)
    assert result.active.comparison_pledge.canonical == "1200.00"
    assert result.financial.comparison.funds == (9,)
    assert result.giving.through_date.isoformat() == "2026-10-15"


def test_missing_latest_pledge_is_unavailable_but_response_is_counted():
    document = observation(count=1)
    document["responses"] = [[1, 4, None]]
    result = calculate(document).active
    assert result.responses == 1 and not result.annual_pledge.available


@pytest.mark.parametrize("count", [False, -1, 1])
def test_incomplete_snapshot_pledge_count_is_not_observed_zero(count):
    document = observation()
    document["source"]["pledge_count"] = count
    with pytest.raises(StatisticsUnavailable):
        calculate(document)


def test_out_of_population_pledge_cannot_become_a_trusted_detached_input():
    document = observation(count=1)
    document["pledges"] = [record("8888.00", family_key="2")]
    document["source"]["counts"]["pledge"] = 1
    document["source"]["pledge_count"] = 1
    with pytest.raises(StatisticsUnavailable):
        calculate(document)


def test_detached_pledges_cannot_exceed_matching_snapshot_count():
    document = observation(count=1)
    document["pledges"] = [record("10.00"), record("20.00")]
    document["source"]["counts"]["pledge"] = 1
    document["source"]["pledge_count"] = 1
    with pytest.raises(StatisticsUnavailable):
        calculate(document)


def test_document_detachment_round_trip_and_private_repr():
    document = observation(count=1)
    document["responses"] = [[1, 4, "1234.56"]]
    frozen = StatisticsInputs.capture(document)
    expected = calculate_statistics(frozen)
    document["responses"].clear()
    detached = frozen.document()
    detached["corpus"]["family"].clear()
    assert calculate_statistics(frozen) == expected
    assert calculate_statistics(StatisticsInputs.capture(frozen.document())) == expected
    assert "head1" not in repr(frozen) and "1234.56" not in repr(frozen)
    assert expected.active.annual_pledge.display == "$1,234.56"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(schema="wrong"),
        lambda d: d.update(campaign_id="private-invalid-identity"),
        lambda d: d.update(observed_at="2026-10-16T04:01:00"),
        lambda d: d.update(submission_watermark=True),
        lambda d: d.update(responses=[[1, 5, "1.00"]]),
        lambda d: d.update(responses=[[1, 1, "1.00"], [1, 2, "2.00"]]),
        lambda d: d.update(responses=[[1, 1, "-1.00"]]),
        lambda d: d.update(responses=[[1, 1, "1000000000.00"]]),
        lambda d: d.update(responses=[[999, 1, "1.00"]]),
        lambda d: d["source"].update(generation=True),
        lambda d: d["source"]["counts"].update(family=99),
        lambda d: d["corpus"]["member"]["1"].update(family_key="999"),
        lambda d: d["corpus"]["family"]["1"].update(email_eligible=False),
        lambda d: d.update(refusals=[[1, "not-an-address"]]),
    ],
)
def test_malformed_trusted_inputs_fail_without_leaking_values(mutate):
    document = observation()
    mutate(document)
    with pytest.raises(StatisticsUnavailable) as error:
        calculate(document)
    assert str(error.value) == "Campaign statistics are unavailable."


def test_strict_input_and_filter_types():
    with pytest.raises(TypeError):
        calculate_statistics({})
    with pytest.raises(TypeError):
        calculate(observation(), include_inactive=1)
    with pytest.raises(ValueError):
        calculate(observation()).active.proportion("arbitrary")


def test_reference_population_has_exact_denominators_and_money():
    document = observation(count=5000)
    document["submission_watermark"] = 5000
    document["responses"] = [[index, index, "10.01"] for index in range(1, 5001)]
    result = calculate(document).active
    assert result.families == result.active_members == result.responses == 5000
    assert result.annual_pledge.canonical == "50050.00"
    assert result.proportion("responses") == "5,000 out of 5,000 (100%)"


def test_source_observation_is_independent_of_later_parish_timezone():
    original = observation()
    changed = deepcopy(original)
    changed["configuration"]["timezone"] = "Pacific/Honolulu"
    assert calculate(original) == calculate(changed)
    assert calculate(original).campaign_id == UUID(original["campaign_id"])
