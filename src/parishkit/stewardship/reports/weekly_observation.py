"""Single-statement live additional-information capture for a weekly report owner.

PostgreSQL MVCC freezes item dispositions, submission sequence and source names
together. Terminal rows are projected without text *inside SQL*: historical
private prose must not leak into detached correction inputs or diagnostics.
This read has no send/retention authority. Its caller must authorize and retain
the observation under fenced ownership before leaving its protected transaction.
"""

import json
from datetime import UTC, datetime
from uuid import UUID

from django.db import connection

from .weekly_digest import WeeklyCorrection, WeeklyInformation
from .weekly_selection import WeeklyItem, WeeklyObservation


class WeeklyUnavailable(ValueError):
    """Invalid private observations fail without disclosing their input values."""


CAPTURE = "SELECT stewardship_weekly_observation_v1(%s)::text"


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


def capture_weekly_observation(campaign_id):
    """Capture all live items, including inactive Families, in one MVCC statement.

    The weekly report is not a Family mailing: source eligibility, consent and
    email deliverability cannot suppress an already submitted request for Staff.
    A disappeared source Family retains its DUID and an honest generic name, not
    an invented current source name. Source rows need no later read for compilation.
    """
    if not isinstance(campaign_id, UUID):
        raise ValueError("Weekly capture requires a canonical campaign identity.")
    with connection.cursor() as cursor:
        cursor.execute(CAPTURE, [campaign_id])
        row = cursor.fetchone()
    if row is None or row[0] is None:
        raise WeeklyUnavailable("Weekly report inputs are unavailable.")
    return decode_observation(json.loads(row[0]))


def observation_document(observation):
    """Serialize a validated frozen observation using the exact SQL projection."""
    if type(observation) is not WeeklyObservation:
        raise TypeError("Weekly retention requires a typed observation.")
    return {
        "campaign_id": str(observation.campaign_id),
        "source_id": str(observation.source_id),
        "configuration_id": str(observation.configuration_id),
        "observed_at": observation.observed_at.isoformat(),
        "watermark": observation.watermark,
        "items": [
            [
                str(item.value.item_id),
                item.sequence,
                item.value.family_duid,
                item.value.family_name,
                item.value.submitted_at.isoformat(),
                "current_actionable"
                if type(item.value) is WeeklyInformation
                else item.value.disposition,
                item.value.text if type(item.value) is WeeklyInformation else None,
            ]
            for item in observation.items
        ],
    }
