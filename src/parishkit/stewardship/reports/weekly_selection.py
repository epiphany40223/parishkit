"""A sent Weekly digest's observation and selection, as its saved page reads them.

The Weekly digest pipeline that captured and selected them is retired
(#1834); these typed values validate what a retained snapshot holds.
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from .weekly_digest import WeeklyCorrection, WeeklyDigestDocument, WeeklyInformation


@dataclass(frozen=True, repr=False)
class WeeklyItem:
    """One live item's submission order and already-projected current disposition."""

    sequence: int
    value: WeeklyInformation | WeeklyCorrection

    def __post_init__(self):
        """Terminal items cannot carry text, and boolean sequences are not integers."""
        if (
            type(self.sequence) is not int
            or self.sequence <= 0
            or type(self.value) not in {WeeklyInformation, WeeklyCorrection}
        ):
            raise ValueError("Weekly selection requires typed live items.")


@dataclass(frozen=True, repr=False)
class WeeklyObservation:
    """One source/configuration/submission observation, not a collection of pages."""

    campaign_id: UUID
    source_id: UUID
    configuration_id: UUID
    observed_at: datetime
    watermark: int
    items: tuple[WeeklyItem, ...]

    def __post_init__(self):
        """Reject mixed, duplicate, unordered or future inputs before selection."""
        if (
            any(
                not isinstance(value, UUID)
                for value in (self.campaign_id, self.source_id, self.configuration_id)
            )
            or type(self.observed_at) is not datetime
            or self.observed_at.utcoffset() is None
            or type(self.watermark) is not int
            or self.watermark < 0
            or type(self.items) is not tuple
            or any(type(item) is not WeeklyItem for item in self.items)
        ):
            raise ValueError("Weekly observation has invalid identity or inputs.")
        sequences = tuple(item.sequence for item in self.items)
        identifiers = {item.value.item_id for item in self.items}
        if (
            sequences != tuple(sorted(set(sequences)))
            or len(identifiers) != len(self.items)
            or any(
                item.sequence > self.watermark
                or item.value.submitted_at > self.observed_at
                for item in self.items
            )
        ):
            raise ValueError("Weekly observation contains inconsistent live items.")


@dataclass(frozen=True, repr=False)
class WeeklySelection:
    """Exact selected content and the capture boundary, including an empty result."""

    observation: WeeklyObservation
    information: tuple[WeeklyInformation, ...]
    corrections: tuple[WeeklyCorrection, ...]

    @property
    def empty(self):
        """An empty result resolves an interval only after its owner commits it."""
        return not self.information and not self.corrections

    def document(
        self,
        *,
        snapshot_id,
        parish_name,
        campaign_name,
        campaign_timezone,
        manual=False,
        date_format=None,
        funnel=None,
        mode=None,
    ):
        """Build the compiler input without querying mutable source or item rows.

        ``funnel`` and ``mode`` are the report's response funnel and system
        mode (see ``WeeklyDigestDocument``); the caller counts the funnel.
        """
        return WeeklyDigestDocument(
            snapshot_id=snapshot_id,
            campaign_id=self.observation.campaign_id,
            parish_name=parish_name,
            campaign_name=campaign_name,
            campaign_timezone=campaign_timezone,
            observed_at=self.observation.observed_at,
            information=self.information,
            corrections=self.corrections,
            manual=manual,
            date_format=date_format,
            funnel=funnel,
            mode=mode,
        )
