"""Detached weekly interval selection, independent of mutable delivery state.

The durable owner supplies one coherent observation and its resolved history.
This module does not confer authority, advance an interval, or infer provider
acceptance from a queued/cancelled message. A submission sequence, not wall-clock
equality, is the boundary: submissions committed after capture belong to the next
interval even if their timestamps happen to equal the preceding observation.
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
class WeeklyHistory:
    """Detached resolved-interval and actual-reporting history for one namespace.

    `reported` includes only items whose actionable content was actually accepted,
    including acceptance from an unfinished earlier occurrence. `corrected` contains
    terminal dispositions covered by completed correction occurrences, which may
    include guarded recipient withdrawals. Withdrawal resolves an obligation; it
    does not make an unmailed actionable item eligible for a later correction.

    The database owner must establish these facts under the same campaign, mode and
    rehearsal epoch. This internal value is not accepted from a browser or broker.
    """

    campaign_id: UUID
    watermark: int = 0
    reported: frozenset[UUID] = frozenset()
    corrected: frozenset[tuple[UUID, str]] = frozenset()

    def __post_init__(self):
        """A correction cannot claim history for an item never actually reported."""
        if (
            not isinstance(self.campaign_id, UUID)
            or type(self.watermark) is not int
            or self.watermark < 0
            or type(self.reported) is not frozenset
            or any(not isinstance(item, UUID) for item in self.reported)
            or type(self.corrected) is not frozenset
            or any(
                type(item) is not tuple
                or len(item) != 2
                or not isinstance(item[0], UUID)
                or type(item[1]) is not str
                or item[1] not in {"superseded", "withdrawn"}
                or item[0] not in self.reported
                for item in self.corrected
            )
        ):
            raise ValueError("Weekly selection requires consistent reporting history.")


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
    ):
        """Build the compiler input without querying mutable source or item rows."""
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
        )


def select_weekly(observation, history):
    """Select new actionable text and unresolved corrections from frozen inputs.

    Accepted partial deliveries do not advance the successful interval. Their new
    items remain selected for unserved recipients; the durable fanout owner uses
    per-recipient accepted item/disposition coverage to avoid sending them twice.
    Old items remain available for corrections after any number of empty intervals.
    Staff follow-up flags/notes deliberately do not participate in this decision.
    """
    if type(observation) is not WeeklyObservation or type(history) is not WeeklyHistory:
        raise TypeError("Weekly selection requires an observation and history.")
    if (
        observation.campaign_id != history.campaign_id
        or history.watermark > observation.watermark
    ):
        raise ValueError("Weekly history differs from the captured campaign interval.")
    information, corrections = [], []
    for item in observation.items:
        value = item.value
        if type(value) is WeeklyInformation:
            if item.sequence > history.watermark:
                information.append(value)
        elif (
            value.item_id in history.reported
            and (value.item_id, value.disposition) not in history.corrected
        ):
            corrections.append(value)

    def order(value):
        """Use the renderer's stable submission-time/identity order."""
        return value.submitted_at, value.item_id.int

    return WeeklySelection(
        observation,
        tuple(sorted(information, key=order)),
        tuple(sorted(corrections, key=order)),
    )
