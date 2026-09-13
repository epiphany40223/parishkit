"""Frozen report-input identity and inclusive campaign-local date ranges."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from .models import POPULATION_SCOPES

DAY_FIELDS = frozenset(
    {
        "local_date",
        "first_responses",
        "cumulative_responses",
        "cohort_denominator",
        "source_generation",
        "source_as_of",
        "population_available",
        "pledge_available",
        "pledge_total",
    }
)


def validate_day(values):
    """Reject lossy internal coercions before SQL checks cross-record consistency."""
    if type(values) is not dict or set(values) != DAY_FIELDS:
        raise ValueError("Fact rows require their complete canonical field set.")
    if type(values["local_date"]) is not date or any(
        type(values[field]) is not bool
        for field in ("population_available", "pledge_available")
    ):
        raise ValueError("Fact dates and availability flags must have exact types.")
    if any(
        type(values[field]) is not int or not 0 <= values[field] < 2**63
        for field in ("first_responses", "cumulative_responses", "cohort_denominator")
    ):
        raise ValueError("Fact counts must be bounded nonnegative integers.")
    generation, instant, amount = (
        values[field] for field in ("source_generation", "source_as_of", "pledge_total")
    )
    if generation is not None and (
        type(generation) is not int or not 1 <= generation < 2**63
    ):
        raise ValueError("Fact source generation must be an exact positive integer.")
    if instant is not None and (
        not isinstance(instant, datetime) or instant.utcoffset() is None
    ):
        raise ValueError("Fact source timestamps must be timezone-aware instants.")
    if amount is not None and (
        not isinstance(amount, Decimal)
        or not amount.is_finite()
        or not Decimal(0) <= amount <= Decimal("9999999999999999.99")
        or amount.as_tuple().exponent < -2
    ):
        raise ValueError("Fact pledges require bounded exact Decimal cents.")


@dataclass(frozen=True)
class FactInputs:
    """Exact reusable input context, including the graph's local-date upper bound."""

    campaign_id: UUID
    population_scope: str
    source_id: UUID
    submission_watermark: int
    timezone_configuration_id: UUID
    through_date: date

    def __post_init__(self):
        """Reject malformed identities, numeric coercion and timestamp confusion."""
        if any(
            not isinstance(value, UUID)
            for value in (
                self.campaign_id,
                self.source_id,
                self.timezone_configuration_id,
            )
        ):
            raise ValueError("Fact inputs require canonical record UUIDs.")
        if self.population_scope not in POPULATION_SCOPES:
            raise ValueError("Unknown fact population scope.")
        if (
            type(self.submission_watermark) is not int
            or not 0 <= self.submission_watermark < 2**63
        ):
            raise ValueError("Fact submission watermark must be a nonnegative integer.")
        if type(self.through_date) is not date:
            raise ValueError("Fact through-date must be a calendar date.")


def expected_dates(start, end, through):
    """Resolve the complete displayed date range, including a pre-start empty chart."""
    if any(type(value) is not date for value in (start, end, through)) or end < start:
        raise ValueError("Fact date inputs require an ordered campaign interval.")
    last = min(end, through)
    if last < start:
        return ()
    return tuple(
        start + timedelta(days=index) for index in range((last - start).days + 1)
    )
