"""Exact current-population statistics over detached, reproducible observations.

The capture owner supplies one coherent source/configuration/submission/refusal
observation. Calculations perform no database or clock I/O. Private minimal
source projections are canonical text, not shallowly frozen mutable dictionaries;
each decode returns a fresh document. BG-07 must retain observations and source
protection under its own durable digest owner before using them asynchronously.
"""

import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from parishkit.stewardship.campaigns.domain import Percentage
from parishkit.stewardship.responses.financial_inputs import (
    FinancialDefinition,
    GivingObservation,
    family_total,
    financial_definition,
    giving_observation,
)
from parishkit.stewardship.source.families import FamilySuppressions, family_recipients
from parishkit.stewardship.web.presentation import out_of

from .money import MoneyAmount, source_cents


class StatisticsUnavailable(ValueError):
    """Invalid/incomplete observations never disclose their private input values."""


def _instant(value):
    """Require an aware serialized instant and normalize it to UTC."""
    result = datetime.fromisoformat(value)
    if result.utcoffset() is None:
        raise ValueError
    return result.astimezone(UTC)


@dataclass(frozen=True)
class StatisticsInputs:
    """An internal private document, not a public export or authorization token."""

    canonical: str = field(repr=False)

    @classmethod
    def capture(cls, document):
        """Detach the caller's mutable values without retaining their references."""
        return cls(json.dumps(document, sort_keys=True, separators=(",", ":")))

    def document(self):
        """Return a fresh copy for an authorized durable owner or recalculation."""
        return json.loads(self.canonical)


@dataclass(frozen=True)
class PopulationStatistics:
    """One labeled population; inactive values never enlarge the active base."""

    families: int
    active_members: int
    eligible_email: int
    deliverable_email: int
    responses: int
    annual_pledge: MoneyAmount
    comparison_pledge: MoneyAmount

    @property
    def no_deliverable_email(self):
        """The postal/outreach complement uses exactly the same Family base."""
        return self.families - self.deliverable_email

    def proportion(self, name):
        """Reuse the application's exact US count/percentage presentation."""
        if name not in {"eligible_email", "deliverable_email", "responses"}:
            raise ValueError("Unknown statistics proportion.")
        return out_of(Percentage(getattr(self, name), self.families))


@dataclass(frozen=True)
class CampaignStatistics:
    """Aggregate results retain their full source and observation provenance."""

    campaign_id: UUID
    configuration_id: UUID
    observed_at: datetime
    source_id: UUID | None
    source_generation: int | None
    source_as_of: datetime | None
    submission_watermark: int
    active: PopulationStatistics | None
    inactive: PopulationStatistics | None
    financial_enabled: bool
    financial: FinancialDefinition | None
    giving: GivingObservation | None


def _calculate(document, *, include_inactive):
    """Validate the exact envelope, then group source/response inputs only once."""
    if document["schema"] != "campaign-statistics-v1":
        raise ValueError
    campaign_id = UUID(document["campaign_id"])
    configuration_id = UUID(document["configuration_id"])
    observed_at = _instant(document["observed_at"])
    watermark = document["submission_watermark"]
    if type(watermark) is not int or not 0 <= watermark < 2**63:
        raise ValueError
    configuration = document["configuration"]
    financial_enabled = "financial" in configuration["modules"]
    financial = (
        financial_definition(configuration, campaign_id=campaign_id)
        if financial_enabled and configuration["financial"] is not None
        else None
    )
    metadata = dict(
        campaign_id=campaign_id,
        configuration_id=configuration_id,
        observed_at=observed_at,
        submission_watermark=watermark,
        financial_enabled=financial_enabled,
        financial=financial,
    )
    source = document["source"]
    if source is None:
        return CampaignStatistics(
            **metadata,
            source_id=None,
            source_generation=None,
            source_as_of=None,
            active=None,
            inactive=None,
            giving=None,
        )
    source_id = UUID(source["id"])
    generation = source["generation"]
    source_as_of = _instant(source["promoted_at"])
    if (
        type(generation) is not int
        or not 0 < generation < 2**63
        or source_as_of > observed_at
    ):
        raise ValueError
    corpus = document["corpus"]
    for kind in ("family", "member"):
        if len(corpus[kind]) != source["counts"][kind]:
            raise ValueError
    recipients = family_recipients(
        corpus,
        suppressed_addresses=FamilySuppressions(
            frozenset((row[0], row[1]) for row in document["refusals"])
        ),
    )
    active = {row.status.duid for row in recipients if row.status.portal_eligible}
    eligible = {row.status.duid for row in recipients if row.status.email_eligible}
    deliverable = {
        row.status.duid for row in recipients if row.status.email_deliverable
    }
    members = Counter()
    for member in corpus["member"].values():
        if (
            type(member["active"]) is not bool
            or member["family_key"] not in corpus["family"]
        ):
            raise ValueError
        if member["active"]:
            members[int(member["family_key"])] += 1
    cohort = set(document["ever_eligible"])
    if any(type(duid) is not int or not 0 < duid < 2**31 for duid in cohort):
        raise ValueError
    responses = {}
    sequences = set()
    for row in document["responses"]:
        duid, sequence, amount = row
        if (
            duid not in cohort
            or duid in responses
            or type(sequence) is not int
            or not 0 < sequence <= watermark
            or sequence in sequences
        ):
            raise ValueError
        value = MoneyAmount(source_cents(amount) if amount is not None else None)
        if value.available and not 0 <= value.cents <= 99_999_999_999:
            raise ValueError
        sequences.add(sequence)
        responses[duid] = value
    giving = giving_observation(source["cursor"], financial) if financial else None
    pledges = defaultdict(list)
    financial_population = active | cohort
    if giving is not None:
        # Complete snapshot coverage is a numeric proof, not permission to copy
        # individual financial values for households outside either population.
        count = source["pledge_count"]
        if (
            type(count) is not int
            or count != source["counts"]["pledge"]
            or len(document["pledges"]) > count
        ):
            raise ValueError
        for row in document["pledges"]:
            if int(row["family_key"]) not in financial_population:
                raise ValueError
            pledges[int(row["family_key"])].append(row)
    comparison = {
        duid: family_total(rows, financial.comparison, family_duid=duid)
        for duid, rows in pledges.items()
    }
    observed_families = {int(key) for key in corpus["family"]}

    def population(identities):
        """Sum exact annual answers, never installment displays or Test versions."""
        responders = identities & responses.keys()
        amounts = [responses[duid] for duid in responders]
        annual = MoneyAmount(
            sum(amount.cents for amount in amounts)
            if financial and all(amount.available for amount in amounts)
            else None
        )
        # Removed Families lack an observation, not merely a pledge row. Their
        # comparison subtotal must stay unavailable instead of inventing zero.
        prior = MoneyAmount(
            sum(comparison.get(duid, MoneyAmount(0)).cents for duid in identities)
            if giving is not None and identities <= observed_families
            else None
        )
        return PopulationStatistics(
            len(identities),
            sum(members[duid] for duid in identities),
            len(identities & eligible),
            len(identities & deliverable),
            len(responders),
            annual,
            prior,
        )

    return CampaignStatistics(
        **metadata,
        source_id=source_id,
        source_generation=generation,
        source_as_of=source_as_of,
        active=population(active),
        inactive=population(cohort - active) if include_inactive else None,
        giving=giving,
    )


def calculate_statistics(inputs, *, include_inactive=False):
    """Reproduce aggregates from frozen inputs; missing source remains unavailable."""
    if not isinstance(inputs, StatisticsInputs) or type(include_inactive) is not bool:
        raise TypeError("Statistics require trusted inputs and an explicit filter.")
    try:
        return _calculate(inputs.document(), include_inactive=include_inactive)
    except (KeyError, TypeError, ValueError, OverflowError, AttributeError):
        raise StatisticsUnavailable("Campaign statistics are unavailable.") from None
