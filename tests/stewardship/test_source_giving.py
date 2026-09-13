"""Financial imports retain only configured periods and never guess ownership."""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from test_parishsoft_source import initialized, page

from parishkit.stewardship.source.canonical import InvalidSourcePayload
from parishkit.stewardship.source.corpus import normalize_core
from parishkit.stewardship.source.giving import _amount, load_giving
from parishkit.stewardship.source.windows import GivingPeriod, RefreshWindow

from .test_source_corpus import TODAY, source


def window(*, year=2026, funds=(9,)):
    """Select one complete annual period; second-window cases provide it explicitly."""
    return RefreshWindow(
        uuid4(), (GivingPeriod(date(year, 1, 1), date(year, 12, 31), funds),)
    )


def pledge(identifier=1, **changes):
    """A pledge can be recorded before its effective financial start date."""
    return {
        "pledgeID": identifier,
        "fundID": 9,
        "organizationID": 5,
        "familyID": 11,
        "memberID": None,
        "currentPledgeAmount": 1200,
        "pledgeDate": "2025-11-15",
        "pledgeStartDate": "2026-01-01",
        **changes,
    }


def contribution(identifier=1, **changes):
    """Contribution DTO spelling differs from the pledge response contract."""
    return {
        "contributionID": identifier,
        "fundId": 9,
        "organizationId": 5,
        "familyId": 1,
        "memberId": 3,
        "contributionAmount": 100.25,
        "contributionDate": "2026-08-01",
        **changes,
    }


def read(
    tmp_path, pledges=(), contributions=(), *, selected=None, data=None, **options
):
    """Exercise real coherent paging/decimal decoding through a synthetic Session."""
    client = initialized(tmp_path, [page(list(pledges)), page(list(contributions))])
    result = load_giving(
        client,
        corpus=normalize_core(source() if data is None else data, as_of=TODAY),
        window=window() if selected is None else selected,
        as_of=TODAY,
        **options,
    )
    return result, client


def test_effective_pledge_date_and_bounded_contribution_query(tmp_path):
    """A November commitment for January remains included with exact cents."""
    result, client = read(tmp_path, [pledge()], [contribution()])
    assert result.pledges["1"] == {
        "schema_version": 1,
        "family_key": "1",
        "fund_key": "9",
        "amount": "1200.00",
        "effective_date": "2026-01-01",
    }
    assert result.contributions["1"]["amount"] == "100.25"
    assert result.anonymous_pledges == result.anonymous_contributions == 0
    pledge_query = client.session.calls[1][2]["params"]
    assert pledge_query == {
        "OrganizationID": 5,
        "FundID": 9,
        "PageSize": 500,
        "PageNumber": 1,
    }
    query = client.session.calls[2][2]["params"]
    assert query["StartDate"] == "2026-01-01" and query["EndDate"] == TODAY.isoformat()
    assert not client.config.cache_dir.exists()


def test_no_financial_window_reads_no_detail(tmp_path):
    """Archived/nonfinancial campaigns cannot expand a source refresh into history."""
    result, client = read(tmp_path, selected=RefreshWindow(None, ()))
    assert not result.pledges and not result.contributions
    assert len(client.session.calls) == 1


def test_future_period_reads_pledges_but_no_future_contributions(tmp_path):
    """Upcoming commitments exist before their giving period begins."""
    result, client = read(
        tmp_path, [pledge(pledgeStartDate="2027-01-01")], selected=window(year=2027)
    )
    assert len(result.pledges) == 1 and not result.contributions
    assert len(client.session.calls) == 2


def test_out_of_period_pledge_history_is_never_returned_for_staging(tmp_path):
    """Selected fund history is transient and never widens the retained window."""
    result, _ = read(
        tmp_path, [pledge(), pledge(2, pledgeStartDate="2022-01-01", familyID=999)]
    )
    assert set(result.pledges) == {"1"}
    assert "999" not in str(result)


def test_explicit_anonymous_and_member_attributed_records(tmp_path):
    """Anonymous amounts are count-only evidence, not assigned to a guessed Family."""
    result, _ = read(
        tmp_path,
        [pledge(familyID=0), pledge(2, familyID=None, memberID=3)],
        [contribution(familyId=None, memberId=None)],
    )
    assert result.anonymous_pledges == result.anonymous_contributions == 1
    assert set(result.pledges) == {"2"} and result.pledges["2"]["family_key"] == "1"
    assert not result.contributions


@pytest.mark.parametrize(
    "changes",
    [
        {"familyID": True},
        {"familyID": "11"},
        {"familyID": 999},
        {"memberID": 999},
        {"familyID": 2, "memberID": 3},
        {"fundID": 10},
        {"organizationID": 6},
        {"pledgeStartDate": None},
        {"currentPledgeAmount": None},
        {"currentPledgeAmount": 1.001},
    ],
)
def test_invalid_pledge_does_not_become_verified_zero(tmp_path, changes):
    """Any in-scope validation failure invalidates the complete giving load."""
    with pytest.raises(InvalidSourcePayload):
        read(tmp_path, [pledge(**changes)])


def test_family_duid_local_id_collision_is_not_guessed(tmp_path):
    """Even one plausible alias must not override contradictory retained identity."""
    data = source()
    data.families[2]["familyID"] = 1
    with pytest.raises(InvalidSourcePayload, match="ambiguous"):
        read(tmp_path, [pledge(familyID=1)], data=data)


def test_provider_cannot_ignore_contribution_date_bounds(tmp_path):
    """A response outside its explicit date query is not a trustworthy complete set."""
    with pytest.raises(InvalidSourcePayload, match="date query"):
        read(tmp_path, contributions=[contribution(contributionDate="2025-01-01")])


@pytest.mark.parametrize(
    "value",
    [True, 1.2, "1.20", None, Decimal("NaN"), Decimal("1e20"), Decimal("1.001")],
)
def test_amounts_require_exact_finite_cent_values(value):
    """Float/string coercion and rounding cannot alter financial source evidence."""
    with pytest.raises(InvalidSourcePayload):
        _amount(value)


def test_negative_adjustments_and_negative_zero_are_exact():
    """Refunds remain negative while insignificant negative-zero spelling is removed."""
    assert _amount(Decimal("-12.30")) == "-12.30"
    assert _amount(Decimal("-0.000")) == "0.00"


def test_missing_selected_fund_fails_before_any_detail_read(tmp_path):
    """An unavailable selected catalog entry is not evidence of no giving."""
    with pytest.raises(InvalidSourcePayload, match="fund is unavailable"):
        read(tmp_path, selected=window(funds=(10,)))


def test_overlapping_periods_deduplicate_unchanged_contributions(tmp_path):
    """An explicitly configured overlap does not double the same provider record."""
    selected = window()
    selected = RefreshWindow(selected.campaign_id, selected.periods * 2)
    client = initialized(
        tmp_path, [page([pledge()]), page([contribution()]), page([contribution()])]
    )
    result = load_giving(
        client,
        corpus=normalize_core(source(), as_of=TODAY),
        window=selected,
        as_of=TODAY,
    )
    assert len(result.pledges) == len(result.contributions) == 1


def test_changed_duplicate_across_period_queries_invalidates_load(tmp_path):
    """A second observation cannot replace the first silently."""
    selected = window()
    selected = RefreshWindow(selected.campaign_id, selected.periods * 2)
    client = initialized(
        tmp_path,
        [
            page([]),
            page([contribution()]),
            page([contribution(contributionAmount=200)]),
        ],
    )
    with pytest.raises(InvalidSourcePayload, match="changed during"):
        load_giving(
            client,
            corpus=normalize_core(source(), as_of=TODAY),
            window=selected,
            as_of=TODAY,
        )


@pytest.mark.parametrize(
    "selected",
    [
        RefreshWindow(
            None, (GivingPeriod(date(2026, 1, 1), date(2026, 12, 31), (9,)),)
        ),
        RefreshWindow(
            uuid4(), (GivingPeriod(date(2026, 1, 1), date(2026, 12, 30), (9,)),)
        ),
        RefreshWindow(
            uuid4(), (GivingPeriod(date(2026, 1, 1), date(2026, 12, 31), (True,)),)
        ),
        RefreshWindow(
            uuid4(), (GivingPeriod(date(2026, 1, 1), date(2026, 12, 31), (9, 9)),)
        ),
        RefreshWindow(
            uuid4(), (GivingPeriod(date(2026, 1, 1), date(2026, 12, 31), ()),)
        ),
    ],
)
def test_invalid_internal_window_cannot_issue_financial_queries(tmp_path, selected):
    """Source detail is always bound to validated campaign dates and fund sets."""
    with pytest.raises(InvalidSourcePayload):
        read(tmp_path, selected=selected)
