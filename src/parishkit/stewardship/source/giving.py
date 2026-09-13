"""Bounded, selected-period giving reads with exact Family-level source records.

Pledge list date-filter semantics are not specified by the provider. Read only
the selected funds under the coherent client's finite limits, then select by
the pledge's effective start date. A pledge recorded in November for January
must not disappear because its creation date precedes the financial period.
No out-of-period detail is returned for staging or written to an HTTP cache.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from uuid import UUID

from parishkit.parishsoft_source import CoherentParishSoftClient
from parishkit.stewardship.campaigns.intervals import financial_period_end

from .canonical import InvalidSourcePayload
from .corpus import _date, _id, _put
from .windows import GivingPeriod, RefreshWindow


@dataclass(frozen=True)
class GivingResult:
    """Exact selected collections plus count-only evidence of anonymous exclusions."""

    pledges: dict
    contributions: dict
    anonymous_pledges: int
    anonymous_contributions: int


def _amount(value):
    """Preserve signed provider adjustments without binary float or silent rounding."""
    if type(value) not in (Decimal, int):
        raise InvalidSourcePayload("Source giving amount is not an exact number.")
    amount = Decimal(value)
    if not amount.is_finite() or abs(amount) >= Decimal("1e20"):
        raise InvalidSourcePayload("Source giving amount is out of range.")
    try:
        cents = amount.quantize(Decimal("0.01"))
    except InvalidOperation:
        raise InvalidSourcePayload("Source giving amount is out of range.") from None
    if cents != amount:
        raise InvalidSourcePayload("Source giving amount has fractional cents.")
    return format(cents if cents else Decimal("0.00"), ".2f")


def _periods(window):
    """Even internal callers cannot request arbitrary or ambiguous source windows."""
    if not isinstance(window, RefreshWindow) or type(window.periods) is not tuple:
        raise InvalidSourcePayload("A validated source window is required.")
    if len(window.periods) > 2 or (
        window.periods and not isinstance(window.campaign_id, UUID)
    ):
        raise InvalidSourcePayload("Source giving requires the current campaign.")
    for period in window.periods:
        if (
            not isinstance(period, GivingPeriod)
            or type(period.start) is not date
            or type(period.end) is not date
            or period.end <= period.start
            or period.start.year == 9999
            or financial_period_end(period.start) != period.end
            or type(period.funds) is not tuple
            or not 1 <= len(period.funds) <= 1000
            or any(type(key) is not int or not 1 <= key < 2**31 for key in period.funds)
            or tuple(sorted(set(period.funds))) != period.funds
        ):
            raise InvalidSourcePayload("Source giving period or funds are invalid.")
    return window.periods


def _aliases(corpus):
    """Resolve provider FamilyId spellings only when DUID/local-ID evidence agrees."""
    aliases = {}
    for key, family in corpus["family"].items():
        for identifier in (family["familyDUID"], family.get("familyID")):
            if identifier not in (None, 0):
                aliases.setdefault(_id(identifier), set()).add(key)
    return aliases


def _family(row, corpus, aliases, *, kind):
    """Anonymous giving has no Family aggregate; unknown/ambiguous references fail."""
    family = row.get("familyID" if kind == "pledge" else "familyId")
    member = row.get("memberID" if kind == "pledge" else "memberId")
    # Explicit zero is the provider's unassigned reference, not a valid DUID.
    for identifier in (family, member):
        if identifier is not None and (type(identifier) is not int or identifier < 0):
            raise InvalidSourcePayload("Source giving ownership is invalid.")
    member_family = None
    if member:
        value = corpus["member"].get(str(_id(member)))
        if value is None:
            raise InvalidSourcePayload("Source giving has no retained Member.")
        member_family = value["family_key"]
    if not family:
        return member_family
    candidates = aliases.get(_id(family), set())
    if len(candidates) != 1:
        raise InvalidSourcePayload("Source giving Family reference is ambiguous.")
    result = next(iter(candidates))
    if member_family is not None and member_family != result:
        raise InvalidSourcePayload("Source giving Family and Member disagree.")
    return result


def _record(row, *, kind, fund, organization_id):
    """Validate reference/date/money before deciding whether a record is in scope."""
    if type(row) is not dict:
        raise InvalidSourcePayload("Source giving entry is not a record.")
    identifier = _id(row.get("pledgeID" if kind == "pledge" else "contributionID"))
    org_field, fund_field = (
        ("organizationID", "fundID")
        if kind == "pledge"
        else ("organizationId", "fundId")
    )
    if _id(row.get(org_field)) != organization_id or _id(row.get(fund_field)) != fund:
        raise InvalidSourcePayload(
            "Source giving does not match its tenant/fund query."
        )
    day = _date(row.get("pledgeStartDate" if kind == "pledge" else "contributionDate"))
    if day is None:
        raise InvalidSourcePayload("Source giving effective date is unavailable.")
    amount = _amount(
        row.get("currentPledgeAmount" if kind == "pledge" else "contributionAmount")
    )
    return identifier, date.fromisoformat(day), amount


def load_giving(client, *, corpus, window, as_of):
    """Return only complete, exact current/comparison-period Family giving.

    Every read uses the coherent client's page/budget checks and owning bounded
    Session. No financial window means no detail reads. A missing selected fund,
    unsupported response or ambiguous in-scope ownership fails the entire load;
    callers must not convert failure into a verified zero aggregate.
    """
    if not isinstance(client, CoherentParishSoftClient) or type(as_of) is not date:
        raise InvalidSourcePayload("Giving requires a coherent source client and date.")
    client._guard()
    periods = _periods(window)
    funds = sorted({fund for period in periods for fund in period.funds})
    if any(str(fund) not in corpus["fund"] for fund in funds):
        raise InvalidSourcePayload("A selected source giving fund is unavailable.")
    aliases = _aliases(corpus)
    result = {"pledge": {}, "contribution": {}}
    anonymous = {"pledge": set(), "contribution": set()}
    seen = {"pledge": {}, "contribution": {}}
    for fund in funds:
        selected = [period for period in periods if fund in period.funds]
        for kind in ("pledge", "contribution"):
            queries = (
                [None]
                if kind == "pledge"
                else [
                    (period.start, min(period.end, as_of))
                    for period in selected
                    if period.start <= as_of
                ]
            )
            for interval in queries:
                parameters = {
                    "OrganizationID"
                    if kind == "pledge"
                    else "OrganizationId": client.expected_organization_id,
                    "FundID" if kind == "pledge" else "FundId": fund,
                }
                if interval is not None:
                    parameters.update(
                        StartDate=interval[0].isoformat(),
                        EndDate=interval[1].isoformat(),
                    )
                endpoint = (
                    "offering/pledge/list"
                    if kind == "pledge"
                    else "offering/contributiondetail/list"
                )
                for row in client.get_paginated(endpoint, parameters):
                    identifier, day, amount = _record(
                        row,
                        kind=kind,
                        fund=fund,
                        organization_id=client.expected_organization_id,
                    )
                    if interval is not None and not interval[0] <= day <= interval[1]:
                        raise InvalidSourcePayload(
                            "Source contribution ignores its date query."
                        )
                    matches = [
                        period
                        for period in selected
                        if period.start <= day <= period.end
                    ]
                    if not matches:
                        continue
                    family = _family(row, corpus, aliases, kind=kind)
                    payload = {
                        "family_key": family,
                        "fund_key": str(fund),
                        "amount": amount,
                        "effective_date": day.isoformat(),
                    }
                    # Overlapping current/comparison queries may repeat the same
                    # record. A changed second observation invalidates coherence.
                    previous = seen[kind].get(identifier)
                    if previous is not None:
                        if previous != payload:
                            raise InvalidSourcePayload(
                                "Source giving changed during the load."
                            )
                        continue
                    seen[kind][identifier] = payload
                    if family is None:
                        anonymous[kind].add(identifier)
                    else:
                        _put(result, kind, str(identifier), payload)
    return GivingResult(
        result["pledge"],
        result["contribution"],
        len(anonymous["pledge"]),
        len(anonymous["contribution"]),
    )
