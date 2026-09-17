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


CAPTURE = """
WITH selected AS MATERIALIZED (
    SELECT c.id, c.active_configuration_id AS configuration_id,
        sc.snapshot_id AS source_id,
        stewardship_campaign_now_v1() AS observed_at,
        COALESCE((SELECT max(s.campaign_sequence)
            FROM stewardship_submission s
            WHERE s.campaign_id=c.id AND s.mode='live'),0) AS watermark
    FROM stewardship_campaign c
    JOIN stewardship_source_current sc ON sc.singleton
    JOIN stewardship_source_snapshot ss ON ss.id=sc.snapshot_id
        AND ss.state='promoted' AND ss.compacted_at IS NULL
    WHERE c.id=%s
), items AS MATERIALIZED (
    SELECT i.id,s.campaign_sequence,s.submitted_at,f.family_duid,i.disposition,
        CASE WHEN i.disposition='current_actionable' THEN i.text ELSE NULL END AS text,
        COALESCE(NULLIF(btrim(p.canonical::jsonb->>'mailingName'),''),
            NULLIF(btrim(concat_ws(' ',
                NULLIF(btrim(p.canonical::jsonb->>'firstName'),''),
                NULLIF(btrim(p.canonical::jsonb->>'lastName'),''))),''),'Family')
            AS family_name
    FROM selected x
    JOIN stewardship_submission s ON s.campaign_id=x.id
        AND s.mode='live' AND s.campaign_sequence<=x.watermark
    JOIN stewardship_additional_information i ON i.submission_id=s.id
    JOIN stewardship_family_campaign f ON f.id=s.family_id
    LEFT JOIN stewardship_snapshot_family m ON m.snapshot_id=x.source_id
        AND m.source_key=f.family_duid::text
    LEFT JOIN stewardship_source_family p ON p.id=m.payload_id
)
SELECT jsonb_build_object(
    'campaign_id',x.id,'configuration_id',x.configuration_id,
    'source_id',x.source_id,'observed_at',x.observed_at,'watermark',x.watermark,
    'items',COALESCE((SELECT jsonb_agg(jsonb_build_array(
        i.id,i.campaign_sequence,i.family_duid,i.family_name,i.submitted_at,
        i.disposition,i.text) ORDER BY i.campaign_sequence) FROM items i),'[]'::jsonb)
)::text FROM selected x
"""


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
    if row is None:
        raise WeeklyUnavailable("Weekly report inputs are unavailable.")
    return decode_observation(json.loads(row[0]))
