"""Single-statement directory population, private filtering and bounded paging.

All interpolated SQL identifiers are fixed here. User values are bound data.
Recipient predicates use the normalized active-head/valid-email source contract
and unresolved organization/Family-scoped refusals, matching family_recipients
and the statistics observation; integration tests enforce that exact complement.
"""

DIRECTORY = """
WITH selected AS MATERIALIZED (
    SELECT c.id,cc.name,
        CASE WHEN c.state='archived' THEN k.source_snapshot_id
             ELSE sc.snapshot_id END AS source_id
    FROM stewardship_campaign c
    JOIN stewardship_campaign_configuration cc ON cc.id=c.active_configuration_id
    LEFT JOIN stewardship_campaign_credentials k ON k.campaign_id=c.id
    LEFT JOIN stewardship_source_current sc ON sc.singleton
    WHERE c.id=%(campaign)s
), source AS MATERIALIZED (
    SELECT x.id,x.name,s.id AS source_id,s.generation AS source_generation,
        s.promoted_at AS source_as_of,s.organization_id
    FROM selected x JOIN stewardship_source_snapshot s ON s.id=x.source_id
    WHERE s.state='promoted' AND s.compacted_at IS NULL
), options AS (
    SELECT %(filters)s::jsonb AS f
), code_matches AS MATERIALIZED (
    SELECT DISTINCT m.family_id FROM stewardship_family_code_mac m
    JOIN jsonb_each_text(%(candidates)s::jsonb) v ON v.key=m.key_id AND v.value=m.digest
    WHERE m.campaign_id=%(campaign)s
), family_source AS MATERIALIZED (
    SELECT m.source_key,p.canonical::jsonb AS value
    FROM source s JOIN stewardship_snapshot_family m ON m.snapshot_id=s.source_id
    JOIN stewardship_source_family p ON p.id=m.payload_id
    WHERE p.canonical::jsonb->'portal_eligible'='true'::jsonb
), members AS NOT MATERIALIZED (
    SELECT p.source_key,p.family_key,p.canonical::jsonb AS value
    FROM source s JOIN stewardship_snapshot_member m ON m.snapshot_id=s.source_id
    JOIN stewardship_source_member p ON p.id=m.payload_id
    WHERE p.canonical::jsonb->'active'='true'::jsonb
), contacts AS NOT MATERIALIZED (
    SELECT m.source_key,p.canonical::jsonb AS value
    FROM source s JOIN stewardship_snapshot_contact m ON m.snapshot_id=s.source_id
    JOIN stewardship_source_contact p ON p.id=m.payload_id
), base AS MATERIALIZED (
    SELECT f.source_key::bigint AS family_duid,i.id AS family_id,
        coalesce(nullif(btrim(f.value->>'mailingName'),''),
            nullif(btrim(concat_ws(' ',f.value->>'firstName',f.value->>'lastName')),''),
            'Family') AS family_name,
        concat_ws(' ',f.value->>'firstName',f.value->>'lastName') AS search_name,
        f.value->>'envelopeNumber' AS envelope,
        coalesce((SELECT jsonb_agg(jsonb_build_object('duid',h.head,
            'name',btrim(concat_ws(' ',m.value->>'firstName',m.value->>'lastName')))
            ORDER BY h.head::bigint)
            FROM jsonb_array_elements_text(f.value->'active_head_duids') h(head)
            JOIN members m ON m.source_key=h.head),'[]'::jsonb) AS heads,
        coalesce((SELECT jsonb_agg(jsonb_build_object('owner',v.owner,'kind',p.key,
            'value',p.value) ORDER BY v.owner,p.key,p.value)
            FROM (
                SELECT 'Family' AS owner,c.value FROM contacts c
                WHERE c.source_key='family:'||f.source_key
                UNION ALL
                SELECT btrim(concat_ws(' ',m.value->>'firstName',m.value->>'lastName')),
                    c.value FROM members m
                    JOIN contacts c ON c.source_key='member:'||m.source_key
                WHERE m.family_key=f.source_key
            ) v CROSS JOIN LATERAL jsonb_each_text(v.value->'phones') p),
            '[]'::jsonb) AS phones,
        coalesce((SELECT p.canonical::jsonb->'fields' FROM source s
            JOIN stewardship_snapshot_address m ON m.snapshot_id=s.source_id
            JOIN stewardship_source_address p ON p.id=m.payload_id
            WHERE m.source_key='family:'||f.source_key||':primary'),
            '{}'::jsonb) AS address,
        jsonb_array_length(f.value->'active_head_duids') AS head_count,
        (SELECT count(*)
            FROM jsonb_array_elements_text(f.value->'active_head_duids') h(head)
            JOIN contacts c ON c.source_key='member:'||h.head
            CROSS JOIN LATERAL jsonb_array_elements(c.value->'emails') e)
            AS address_count,
        recipients.eligible>0 AS email_eligible,
        recipients.deliverable>0 AS email_deliverable,
        EXISTS(SELECT 1 FROM stewardship_submission r WHERE r.family_id=i.id
            AND r.campaign_id=%(campaign)s AND r.mode='live') AS responded
    FROM family_source f CROSS JOIN source s
    LEFT JOIN stewardship_family_campaign i
        ON i.campaign_id=s.id AND i.family_duid=f.source_key::bigint
    CROSS JOIN LATERAL (
        SELECT count(*) AS eligible,count(*) FILTER (WHERE NOT EXISTS (
            SELECT 1 FROM stewardship_recipient_refusal r
            WHERE r.organization_id=s.organization_id
              AND r.family_duid=f.source_key::bigint
              AND r.address=e->>'value' AND NOT EXISTS (
                  SELECT 1 FROM stewardship_recipient_resolution z
                  WHERE z.refusal_id=r.id)
        )) AS deliverable
        FROM jsonb_array_elements_text(f.value->'active_head_duids') h(head)
        JOIN contacts c ON c.source_key='member:'||h.head
        CROSS JOIN LATERAL jsonb_array_elements(c.value->'emails') e
        WHERE e->'valid'='true'::jsonb
    ) recipients
), rows AS MATERIALIZED (
    SELECT *,CASE WHEN email_deliverable THEN 'deliverable'
        WHEN head_count=0 THEN 'no_head' WHEN address_count=0 THEN 'no_address'
        WHEN NOT email_eligible THEN 'invalid_address'
        ELSE 'provider_refused' END AS reason
    FROM base
), filtered AS MATERIALIZED (
    SELECT r.* FROM rows r CROSS JOIN options o
    WHERE (NOT %(postal)s OR NOT email_deliverable)
      AND (NOT %(exact)s OR family_id IN (SELECT family_id FROM code_matches))
      AND (o.f->>'reason'='any' OR reason=o.f->>'reason')
      AND (o.f->>'phone'='any' OR (jsonb_array_length(phones)>0)=(o.f->>'phone'='yes'))
      AND (o.f->>'response'='any' OR responded=(o.f->>'response'='yes'))
      AND (o.f->>'search'='' OR position(lower(o.f->>'search') IN lower(family_name))>0
          OR position(lower(o.f->>'search') IN lower(search_name))>0
          OR position(o.f->>'search' IN family_duid::text)>0
          OR EXISTS(SELECT 1 FROM jsonb_each_text(address) a
              WHERE position(lower(o.f->>'search') IN lower(a.value))>0))
), ordered AS (
    SELECT r.*,row_number() OVER (ORDER BY
        CASE WHEN o.f->>'sort'='name' THEN lower(family_name) END,
        CASE WHEN o.f->>'sort'='name_desc' THEN lower(family_name) END DESC,
        family_duid) AS ordinal
    FROM filtered r CROSS JOIN options o
), page AS (
    SELECT * FROM ordered ORDER BY ordinal LIMIT %(size)s OFFSET %(offset)s
)
SELECT jsonb_build_object('metadata',to_jsonb(s),
    'total',(SELECT count(*) FROM filtered),
    'active_total',(SELECT count(*) FROM rows),
    'postal_total',(SELECT count(*) FROM rows WHERE NOT email_deliverable),
    'code_matches',(SELECT count(*) FROM code_matches),
    'rows',coalesce((SELECT jsonb_agg(
        to_jsonb(p)-ARRAY['ordinal','head_count','address_count','search_name']
        ORDER BY ordinal) FROM page p),'[]'::jsonb))::text FROM source s
"""
