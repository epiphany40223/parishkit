"""Detached exact-generation participation values shared by all render formats.

No source/submission calculation or authorization belongs here. The owning
export/digest service loads one retained ready generation under its guards and
passes a complete immutable document. Missing observations remain missing.
"""

import csv
import io
import unicodedata
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from parishkit.stewardship.campaigns.domain import Percentage
from parishkit.stewardship.web.exports import csv_cell
from parishkit.stewardship.web.presentation import out_of

from .inputs import validate_day

SCOPE_LABELS = {"historical": "Historical as of day", "current": "Current population"}


def _label(value):
    """Allow bounded plain branding text, never control characters or markup."""
    if (
        type(value) is not str
        or not value.strip()
        or len(value) > 254
        or any(unicodedata.category(char).startswith("C") for char in value)
    ):
        raise ValueError("Report labels require bounded plain text.")
    return value


def _instant(value):
    """Validate an exact instant without accepting a naive datetime as UTC."""
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("Report timestamps require timezone-aware instants.")


@dataclass(frozen=True)
class ParticipationDay:
    """An exact stored fact, including availability and its own source cutoff."""

    local_date: date
    first_responses: int
    cumulative_responses: int
    cohort_denominator: int
    source_generation: int | None
    source_as_of: datetime | None
    population_available: bool
    pledge_available: bool
    pledge_total: Decimal | None

    def __post_init__(self):
        """Mirror fact invariants before serializing values outside PostgreSQL."""
        validate_day(asdict(self))
        if not (
            self.first_responses <= self.cumulative_responses <= self.cohort_denominator
        ):
            raise ValueError("Report participation counts are inconsistent.")
        if self.population_available:
            if self.source_generation is None or self.source_as_of is None:
                raise ValueError("Available population requires source provenance.")
        elif any(
            value is not None for value in (self.source_generation, self.source_as_of)
        ) or any(
            (self.first_responses, self.cumulative_responses, self.cohort_denominator)
        ):
            raise ValueError("Unavailable population cannot contain observations.")
        if self.pledge_available != (self.pledge_total is not None) or (
            self.pledge_available and not self.population_available
        ):
            raise ValueError("Report pledge availability is inconsistent.")

    @property
    def participation(self):
        """Keep known zero distinct from missing, with the shared percent rule."""
        if not self.population_available:
            return "Unavailable"
        return out_of(Percentage(self.cumulative_responses, self.cohort_denominator))


@dataclass(frozen=True)
class ParticipationDocument:
    """A complete frozen ready generation; no implicit clock or pointer lookups."""

    campaign_id: UUID
    fact_set_id: UUID
    parish_name: str
    campaign_name: str
    population_scope: str
    campaign_timezone: str
    browser_timezone: str
    source_generation: int
    source_as_of: datetime
    submission_watermark: int
    requested_at: datetime
    first_date: date | None
    last_date: date | None
    financial_enabled: bool
    days: tuple[ParticipationDay, ...]

    def __post_init__(self):
        """Reject mixed, reordered or incomplete series rather than repairing them."""
        if any(
            not isinstance(value, UUID)
            for value in (self.campaign_id, self.fact_set_id)
        ):
            raise ValueError("Report identity requires canonical UUIDs.")
        _label(self.parish_name)
        _label(self.campaign_name)
        if type(self.population_scope) is not str or self.population_scope not in (
            SCOPE_LABELS
        ):
            raise ValueError("Unknown report population scope.")
        for value in (self.campaign_timezone, self.browser_timezone):
            try:
                if type(value) is not str:
                    raise ValueError("Report timezone must be a name.")
                ZoneInfo(value)
            except (ZoneInfoNotFoundError, ValueError) as error:
                raise ValueError("Report timezone must be an IANA name.") from error
        if (
            type(self.source_generation) is not int
            or not 1 <= self.source_generation < 2**63
            or type(self.submission_watermark) is not int
            or not 0 <= self.submission_watermark < 2**63
            or type(self.financial_enabled) is not bool
        ):
            raise ValueError("Report input cutoffs and flags must have exact types.")
        _instant(self.source_as_of)
        _instant(self.requested_at)
        if type(self.days) is not tuple or any(
            not isinstance(day, ParticipationDay) for day in self.days
        ):
            raise ValueError("Report days must be immutable canonical observations.")
        if not self.days:
            if self.first_date is not None or self.last_date is not None:
                raise ValueError("An empty report cannot claim a displayed interval.")
            return
        if (
            type(self.first_date) is not date
            or type(self.last_date) is not date
            or self.last_date < self.first_date
            or (self.last_date - self.first_date).days + 1 != len(self.days)
        ):
            raise ValueError("Report dates do not describe the complete generation.")
        for index, day in enumerate(self.days):
            if day.local_date != self.first_date + timedelta(days=index):
                raise ValueError("Report dates must be consecutive and sorted.")
            if day.source_generation is not None and (
                day.source_generation > self.source_generation
                or day.source_as_of > self.source_as_of
            ):
                raise ValueError(
                    "Report days cannot exceed their pinned source cutoff."
                )

    @property
    def scope_label(self):
        """Use the same human-readable population scope in every output format."""
        return SCOPE_LABELS[self.population_scope]

    @property
    def as_of_label(self):
        """Localize instants only; graph calendar dates stay in campaign time."""
        zone = ZoneInfo(self.browser_timezone)
        source = self.source_as_of.astimezone(zone).isoformat(timespec="seconds")
        requested = self.requested_at.astimezone(zone).isoformat(timespec="seconds")
        return (
            f"Source #{self.source_generation:,} as of {source}; "
            f"submission cutoff {self.submission_watermark:,}\n"
            f"Requested {requested} ({self.browser_timezone})"
        )


def participation_table(document):
    """Return exact accessible/hover values, with no binary monetary conversion."""
    if not isinstance(document, ParticipationDocument):
        raise TypeError("Rendering requires an immutable participation document.")
    rows = []
    for day in document.days:
        row = {
            "date": day.local_date.isoformat(),
            "scope": document.scope_label,
            "first_responses": day.first_responses
            if day.population_available
            else None,
            "cumulative_responses": (
                day.cumulative_responses if day.population_available else None
            ),
            "cohort_denominator": (
                day.cohort_denominator if day.population_available else None
            ),
            "participation": day.participation,
            "source_generation": day.source_generation,
            "source_as_of": (
                day.source_as_of.astimezone(UTC).isoformat()
                if day.source_as_of is not None
                else None
            ),
        }
        if document.financial_enabled:
            row["pledge_usd"] = (
                format(day.pledge_total, ".2f") if day.pledge_available else None
            )
        rows.append(row)
    return tuple(rows)


def participation_csv(document, output):
    """Write complete exact values with repeated pinned metadata, never page slices."""
    rows = participation_table(document)
    headings = [
        "date",
        "scope",
        "first_responses",
        "cumulative_responses",
        "cohort_denominator",
        "participation",
        "source_generation",
        "source_as_of",
    ]
    if document.financial_enabled:
        headings.append("pledge_usd")
    metadata = {
        "campaign_id": document.campaign_id,
        "fact_set_id": document.fact_set_id,
        "campaign_timezone": document.campaign_timezone,
        "browser_timezone": document.browser_timezone,
        "input_source_generation": document.source_generation,
        "input_source_as_of": document.source_as_of.astimezone(UTC).isoformat(),
        "submission_watermark": document.submission_watermark,
        "requested_at": document.requested_at.astimezone(UTC).isoformat(),
    }
    # A newline-neutral wrapper gives canonical CRLF on every developer host.
    # Detach it so this function never closes the caller-owned artifact stream.
    wrapper = io.TextIOWrapper(output, encoding="utf-8", newline="", write_through=True)
    try:
        writer = csv.writer(wrapper, lineterminator="\r\n")
        writer.writerow(headings + list(metadata))
        trailer = [csv_cell(value) for value in metadata.values()]
        for row in rows:
            writer.writerow([csv_cell(row[key]) for key in headings] + trailer)
        wrapper.flush()
    finally:
        wrapper.detach()
