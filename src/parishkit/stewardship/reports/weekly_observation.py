"""Decode a sent Weekly digest's retained observation for its saved page.

The observation is the projection ``stewardship_weekly_observation_v1``
captured when the digest was prepared (the capture retired with the Weekly
pipeline, #1834). Terminal rows carry no text: historical private prose
must not leak into correction inputs or diagnostics.
"""

from datetime import UTC, datetime
from uuid import UUID

from .weekly_digest import WeeklyCorrection, WeeklyInformation
from .weekly_selection import WeeklyItem, WeeklyObservation


class WeeklyUnavailable(ValueError):
    """Invalid private observations fail without disclosing their input values."""


def _instant(value):
    """Normalize a stored UTC instant, rejecting naive or non-string input."""
    if type(value) is not str:
        raise ValueError
    result = datetime.fromisoformat(value)
    if result.utcoffset() is None:
        raise ValueError
    return result.astimezone(UTC)


def decode_observation(document):
    """Validate the complete internal projection without returning private errors."""
    try:
        if (
            type(document) is not dict
            or set(document)
            != {
                "campaign_id",
                "configuration_id",
                "source_id",
                "observed_at",
                "watermark",
                "items",
            }
            or type(document["items"]) is not list
        ):
            raise ValueError
        items = []
        for row in document["items"]:
            if type(row) is not list or len(row) != 7:
                raise ValueError
            identifier, sequence, duid, name, submitted, disposition, text = row
            identity = UUID(identifier), duid, name, _instant(submitted)
            if disposition == "current_actionable":
                value = WeeklyInformation(*identity, text)
            elif text is None:
                value = WeeklyCorrection(*identity, disposition)
            else:
                raise ValueError
            items.append(WeeklyItem(sequence, value))
        return WeeklyObservation(
            campaign_id=UUID(document["campaign_id"]),
            source_id=UUID(document["source_id"]),
            configuration_id=UUID(document["configuration_id"]),
            observed_at=_instant(document["observed_at"]),
            watermark=document["watermark"],
            items=tuple(items),
        )
    except (TypeError, ValueError, AttributeError, OverflowError):
        raise WeeklyUnavailable("Weekly report inputs are unavailable.") from None
