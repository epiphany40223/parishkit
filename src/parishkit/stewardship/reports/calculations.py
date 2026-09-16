"""Deterministic participation calculations over detached, exact-cutoff inputs.

This module never looks up today's population, effective-response pointers, or
the parish's current timezone. Its caller supplies immutable campaign settings,
permanent promotion/cohort provenance and live responses through one watermark.
The same calculation is used for materialization and drift verification.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime
from uuid import UUID

from parishkit.stewardship.campaigns.intervals import local_day

from .inputs import expected_dates
from .money import MoneyAmount
from .participation import ParticipationDay


def _instant(value):
    """Normalize comparisons so DST folds cannot compare as equal wall times."""
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("Calculation provenance requires an aware instant.")
    return value.astimezone(UTC)


def _positive(value):
    """Reject boolean identities and unbounded numeric coercions."""
    if type(value) is not int or not 0 < value < 2**63:
        raise ValueError("Calculation identity requires a positive integer.")


@dataclass(frozen=True)
class Promotion:
    """Permanent source provenance, independent of retained membership payloads."""

    generation: int
    promoted_at: datetime

    def __post_init__(self):
        """Keep source identity and timestamps exact before selecting day cutoffs."""
        _positive(self.generation)
        object.__setattr__(self, "promoted_at", _instant(self.promoted_at))


@dataclass(frozen=True)
class CohortFamily:
    """A Family's immutable first eligibility, not its mutable current status."""

    family_id: UUID
    first_eligible_at: datetime
    first_eligible_generation: int

    def __post_init__(self):
        """Validate the paired provenance before computing historical membership."""
        if not isinstance(self.family_id, UUID):
            raise ValueError("Calculation Family identity requires a UUID.")
        _positive(self.first_eligible_generation)
        object.__setattr__(self, "first_eligible_at", _instant(self.first_eligible_at))


@dataclass(frozen=True)
class LiveResponse:
    """One immutable live version; annual money is never a rounded installment."""

    family_id: UUID
    sequence: int
    submitted_at: datetime
    pledge: MoneyAmount

    def __post_init__(self):
        """Fail on malformed provenance or a negative Family pledge."""
        if not isinstance(self.family_id, UUID) or not isinstance(
            self.pledge, MoneyAmount
        ):
            raise ValueError("Calculation responses require canonical typed values.")
        _positive(self.sequence)
        object.__setattr__(self, "submitted_at", _instant(self.submitted_at))
        if self.pledge.available and not 0 <= self.pledge.cents <= 99_999_999_999:
            raise ValueError("A Family pledge is outside its supported bounds.")


@dataclass(frozen=True)
class Calculation:
    """Complete detached inputs, suitable for rebuilding one exact fact generation."""

    population_scope: str
    start_date: date
    end_date: date
    through_date: date
    timezone: str
    financial_enabled: bool
    source: Promotion
    submission_watermark: int
    promotions: tuple[Promotion, ...]
    families: tuple[CohortFamily, ...]
    current_family_ids: frozenset[UUID]
    responses: tuple[LiveResponse, ...]

    def __post_init__(self):
        """Reject incomplete/mixed contexts instead of silently repairing inputs."""
        if self.population_scope not in {"historical", "current"} or (
            type(self.financial_enabled) is not bool
            or type(self.submission_watermark) is not int
            or not 0 <= self.submission_watermark < 2**63
            or not isinstance(self.source, Promotion)
        ):
            raise ValueError("Calculation scope and cutoffs must be explicit.")
        expected_dates(self.start_date, self.end_date, self.through_date)
        local_day(self.start_date, self.timezone)
        for values, kind, identity in (
            (self.promotions, Promotion, "generation"),
            (self.families, CohortFamily, "family_id"),
            (self.responses, LiveResponse, "sequence"),
        ):
            if type(values) is not tuple or any(
                not isinstance(value, kind) for value in values
            ):
                raise ValueError("Calculation inputs must be immutable typed tuples.")
            if len({getattr(value, identity) for value in values}) != len(values):
                raise ValueError("Calculation identities must not repeat.")
        family_ids = {family.family_id for family in self.families}
        if (
            type(self.current_family_ids) is not frozenset
            or not self.current_family_ids <= family_ids
            or any(response.family_id not in family_ids for response in self.responses)
            or self.source not in self.promotions
            or any(row.generation > self.source.generation for row in self.promotions)
            or any(row.sequence > self.submission_watermark for row in self.responses)
        ):
            raise ValueError("Calculation inputs do not belong to their frozen cutoff.")
        latest_instants = {}
        for response in sorted(self.responses, key=lambda row: row.sequence):
            prior = latest_instants.get(response.family_id)
            if prior is not None and response.submitted_at < prior:
                raise ValueError("Family response chronology contradicts its sequence.")
            latest_instants[response.family_id] = response.submitted_at


def calculate_participation(inputs):
    """Return complete daily facts for one scope without any database or clock I/O.

    Sort live versions once and advance through instants once. A repeat version
    changes the effective pledge, never the Family's first response. Historical
    membership uses permanent first-eligibility evidence; current membership is
    the same pinned snapshot population at every point. Day ends are exclusive,
    including short/long DST days and entirely skipped calendar dates.
    """
    if not isinstance(inputs, Calculation):
        raise TypeError("Participation requires a detached calculation context.")
    versions = iter(
        sorted(inputs.responses, key=lambda row: (row.submitted_at, row.sequence))
    )
    pending = next(versions, None)
    first, latest, result = {}, {}, []
    for day in expected_dates(inputs.start_date, inputs.end_date, inputs.through_date):
        interval = local_day(day, inputs.timezone).interval
        while pending is not None and pending.submitted_at < interval.end:
            prior = first.get(pending.family_id)
            if prior is None or pending.sequence < prior.sequence:
                first[pending.family_id] = pending
            prior = latest.get(pending.family_id)
            if prior is None or pending.sequence > prior.sequence:
                latest[pending.family_id] = pending
            pending = next(versions, None)
        source = inputs.source
        if inputs.population_scope == "historical":
            source = max(
                (row for row in inputs.promotions if row.promoted_at < interval.end),
                key=lambda row: row.generation,
                default=None,
            )
        if source is None:
            result.append(
                ParticipationDay(day, 0, 0, 0, None, None, False, False, None)
            )
            continue
        cohort = (
            inputs.current_family_ids
            if inputs.population_scope == "current"
            else frozenset(
                family.family_id
                for family in inputs.families
                if family.first_eligible_generation <= source.generation
                and family.first_eligible_at < interval.end
            )
        )
        participants = cohort & first.keys()
        amounts = [latest[family].pledge for family in participants]
        money = MoneyAmount(
            sum(amount.cents for amount in amounts)
            if inputs.financial_enabled and all(amount.available for amount in amounts)
            else None
        )
        result.append(
            ParticipationDay(
                day,
                sum(
                    interval.contains(first[family].submitted_at)
                    for family in participants
                ),
                len(participants),
                len(cohort),
                source.generation,
                source.promoted_at,
                True,
                money.available,
                money.decimal,
            )
        )
    return tuple(result)
