"""Single-statement private input capture and authorized current statistics reads.

READ COMMITTED does not give multiple SELECTs the same snapshot. Capture source,
configuration, latest live responses and unresolved refusals together, detaching
only the projections required for calculations. PostgreSQL MVCC keeps rows
visible through this statement even if compaction commits concurrently. No
FOR SHARE or source read_snapshot is used inside the read-only campaign guard.
"""

import json
from contextlib import contextmanager
from dataclasses import dataclass, field
from uuid import UUID

from django.db import connection

from parishkit.stewardship.campaigns.read_guards import CampaignReadGuard

from .export_services import admit_campaign, authorize
from .statistics import (
    CampaignStatistics,
    StatisticsInputs,
    StatisticsUnavailable,
    calculate_statistics,
)


def _corpus_projection(kind, fields):
    """Compile only hard-coded identifiers; no request value becomes SQL text."""
    pairs = ",".join(f"'{name}',p.payload->'{name}'" for name in fields)
    return (
        "COALESCE((SELECT jsonb_object_agg(p.source_key,"
        f"jsonb_build_object({pairs})) FROM {kind}_payload p),'{{}}'::jsonb)"
    )


FAMILIES = _corpus_projection(
    "family",
    (
        "schema_version",
        "active",
        "parishioner",
        "portal_eligible",
        "email_eligible",
        "active_head_duids",
    ),
)
MEMBERS = _corpus_projection("member", ("schema_version", "family_key", "active"))
CONTACTS = _corpus_projection(
    "contact", ("schema_version", "owner_kind", "owner_key", "emails")
)

# Archived giving cannot follow a successor's source window. Its last reconciled
# campaign snapshot remains explicit; if that membership is unavailable we fail
# closed, never claim a later source's missing giving is an observed zero.
CAPTURE = f"""
WITH selected AS MATERIALIZED (
    SELECT c.id, p.id AS configuration_id, p.values AS configuration,
        CASE WHEN c.state='archived' THEN pc.source_snapshot_id
             ELSE sc.snapshot_id END AS source_id,
        COALESCE((SELECT max(r.campaign_sequence)
            FROM stewardship_submission r
            WHERE r.campaign_id=c.id AND r.mode='live'),0) AS watermark,
        stewardship_campaign_now_v1() AS observed_at
    FROM stewardship_campaign c
    JOIN stewardship_campaign_configuration p ON p.id=c.active_configuration_id
    LEFT JOIN stewardship_campaign_credentials pc ON pc.campaign_id=c.id
    LEFT JOIN stewardship_source_current sc ON sc.singleton
    WHERE c.id=%s
), selected_source AS MATERIALIZED (
    SELECT s.id,s.organization_id,s.generation,s.promoted_at,s.counts,s.cursor
    FROM selected x JOIN stewardship_source_snapshot s ON s.id=x.source_id
    WHERE s.state='promoted' AND s.compacted_at IS NULL
), family_payload AS MATERIALIZED (
    SELECT m.source_key,p.canonical::jsonb AS payload
    FROM selected_source s JOIN stewardship_snapshot_family m ON m.snapshot_id=s.id
    JOIN stewardship_source_family p ON p.id=m.payload_id
), member_payload AS MATERIALIZED (
    SELECT m.source_key,p.canonical::jsonb AS payload
    FROM selected_source s JOIN stewardship_snapshot_member m ON m.snapshot_id=s.id
    JOIN stewardship_source_member p ON p.id=m.payload_id
), active_heads AS MATERIALIZED (
    SELECT f.source_key AS family_key,
        jsonb_array_elements_text(f.payload->'active_head_duids') AS head_key
    FROM family_payload f WHERE f.payload->'portal_eligible'='true'::jsonb
        AND f.payload->'email_eligible'='true'::jsonb
), head_contact_payload AS MATERIALIZED (
    SELECT m.source_key,p.canonical::jsonb AS payload
    FROM selected_source s JOIN stewardship_snapshot_contact m ON m.snapshot_id=s.id
    JOIN stewardship_source_contact p ON p.id=m.payload_id
    WHERE p.owner_kind='member' AND p.owner_key IN (SELECT head_key FROM active_heads)
), contact_payload AS MATERIALIZED (
    SELECT c.source_key,jsonb_set(c.payload,'{{emails}}',
        COALESCE((SELECT jsonb_agg(e ORDER BY e->>'value')
            FROM jsonb_array_elements(c.payload->'emails') e
            WHERE e->'valid'='true'::jsonb),'[]'::jsonb)) AS payload
    FROM head_contact_payload c
), head_addresses AS MATERIALIZED (
    SELECT DISTINCT h.family_key,e->>'value' AS address
    FROM active_heads h
    JOIN contact_payload c ON c.source_key='member:' || h.head_key
    CROSS JOIN LATERAL jsonb_array_elements(c.payload->'emails') e
), report_families AS MATERIALIZED (
    SELECT f.source_key AS family_key FROM family_payload f
    WHERE f.payload->'portal_eligible'='true'::jsonb
    UNION
    SELECT f.family_duid::text FROM selected x
    JOIN stewardship_family_campaign f ON f.campaign_id=x.id
    WHERE f.first_eligible_at IS NOT NULL
), latest AS (
    SELECT DISTINCT ON (r.family_id)
        f.family_duid, r.campaign_sequence, r.annual_pledge
    FROM selected x
    JOIN stewardship_submission r ON r.campaign_id=x.id
        AND r.mode='live' AND r.campaign_sequence<=x.watermark
    JOIN stewardship_family_campaign f ON f.id=r.family_id
    ORDER BY r.family_id, r.campaign_sequence DESC
)
SELECT jsonb_build_object(
    'schema','campaign-statistics-v1',
    'campaign_id',x.id, 'configuration_id',x.configuration_id,
    'configuration',x.configuration, 'observed_at',x.observed_at,
    'submission_watermark',x.watermark,
    'source',CASE WHEN s.id IS NULL THEN NULL ELSE jsonb_build_object(
        'id',s.id,'generation',s.generation,'promoted_at',s.promoted_at,
        'counts',s.counts,'cursor',s.cursor,
        'pledge_count',(SELECT count(*) FROM stewardship_snapshot_pledge m
            WHERE m.snapshot_id=s.id)) END,
    'corpus',jsonb_build_object(
        'family',{FAMILIES}, 'member',{MEMBERS}, 'contact',{CONTACTS}),
    'ever_eligible',COALESCE((SELECT jsonb_agg(f.family_duid ORDER BY f.family_duid)
        FROM stewardship_family_campaign f WHERE f.campaign_id=x.id
        AND f.first_eligible_at IS NOT NULL),'[]'::jsonb),
    'responses',COALESCE((SELECT jsonb_agg(jsonb_build_array(
        r.family_duid,r.campaign_sequence,r.annual_pledge::text)
        ORDER BY r.family_duid) FROM latest r),'[]'::jsonb),
    'refusals',COALESCE((SELECT jsonb_agg(
        DISTINCT jsonb_build_array(r.family_duid,r.address)
        ORDER BY jsonb_build_array(r.family_duid,r.address))
        FROM stewardship_recipient_refusal r WHERE r.organization_id=s.organization_id
        AND EXISTS (SELECT 1 FROM head_addresses h
            WHERE h.family_key=r.family_duid::text
                AND h.address=r.address)
        AND NOT EXISTS (SELECT 1 FROM stewardship_recipient_resolution q
            WHERE q.refusal_id=r.id)),'[]'::jsonb),
    'pledges',COALESCE((SELECT jsonb_agg(p.canonical::jsonb ORDER BY m.source_key)
        FROM stewardship_snapshot_pledge m
        JOIN stewardship_source_pledge p ON p.id=m.payload_id
        WHERE m.snapshot_id=s.id AND x.configuration->'modules' ? 'financial'
            AND p.family_key IN (SELECT family_key FROM report_families)
            AND p.fund_key IN (SELECT jsonb_array_elements_text(
                x.configuration->'financial'->'comparison_fund_duids'))
            AND p.canonical::jsonb->>'effective_date' BETWEEN
                x.configuration->'financial'->>'comparison_start' AND
                x.configuration->'financial'->>'comparison_end'),
        '[]'::jsonb)
)::text
FROM selected x
LEFT JOIN selected_source s ON s.id=x.source_id
"""


def capture_statistics(campaign_id):
    """Internal capture: the caller owns authorization and campaign read protection.

    A future asynchronous digest owner must retain this exact document and its
    source pin transactionally with its occurrence. A detached Python object is
    not durable ownership, and this read service creates no queued work.
    """
    if not isinstance(campaign_id, UUID):
        raise ValueError("Statistics require a canonical campaign identity.")
    with connection.cursor() as cursor:
        cursor.execute(CAPTURE, (campaign_id,))
        row = cursor.fetchone()
    if row is None:
        raise StatisticsUnavailable("Campaign statistics are unavailable.")
    return StatisticsInputs.capture(json.loads(row[0]))


@dataclass(frozen=True)
class StatisticsSelection:
    """Private exact inputs accompany the aggregate, never an implicit new query."""

    inputs: StatisticsInputs = field(repr=False)
    statistics: CampaignStatistics


@contextmanager
def statistics_report(store, user_id, *, campaign_id, include_inactive=False, abort):
    """Hold fresh Admin/Staff admission and read protection through consumption."""
    if any(not isinstance(value, UUID) for value in (campaign_id, user_id)):
        raise ValueError("Report identities must be canonical UUIDs.")
    if type(include_inactive) is not bool:
        raise ValueError("The inactive population option must be explicit.")

    def fresh(guard):
        """Current policy is checked again before any aggregate is handed off."""
        authorize(store, user_id)
        admit_campaign(campaign_id, mutating=False)

    with CampaignReadGuard([campaign_id], authorize=fresh, abort=abort) as guard:
        inputs = capture_statistics(campaign_id)
        result = calculate_statistics(inputs, include_inactive=include_inactive)
        fresh(guard)
        guard.check()
        yield StatisticsSelection(inputs, result)
        guard.check()
