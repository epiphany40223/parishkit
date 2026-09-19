"""One MVCC selection for scoped Ministry counts and bounded private detail.

All values are bound parameters. Only authorized, paginated join rows reach
contact projection; summary and leave rows never fetch contact payloads. Keep
latest-intent selection before state filtering, including hidden Ministries.
"""

MINISTRY_REPORT = """
WITH selected AS MATERIALIZED (
    SELECT c.id,cc.name,cc.timezone,cc.values,
        CASE WHEN c.state='archived' THEN cc.configuration_id
             ELSE sys.active_configuration_id END AS configuration_id,
        CASE WHEN c.state='archived' THEN k.source_snapshot_id
             ELSE sc.snapshot_id END AS source_id
    FROM stewardship_campaign c
    JOIN stewardship_campaign_configuration cc ON cc.id=c.active_configuration_id
    CROSS JOIN stewardship_system_configuration sys
    LEFT JOIN stewardship_campaign_credentials k ON k.campaign_id=c.id
    LEFT JOIN stewardship_source_current sc ON sc.singleton
    WHERE c.id=%(campaign)s
), source AS MATERIALIZED (
    SELECT x.*,s.organization_id,s.generation AS source_generation,
        s.promoted_at AS source_as_of,statement_timestamp() AS observed_at,
        (statement_timestamp() AT TIME ZONE x.timezone)::date AS report_date
    FROM selected x JOIN stewardship_source_snapshot s ON s.id=x.source_id
    WHERE s.state='promoted' AND s.compacted_at IS NULL
        AND x.values->'modules' ? 'ministry'
), ministries AS MATERIALIZED (
    SELECT n.duid::integer AS duid,
        coalesce(nullif(btrim(p.canonical::jsonb->>'name'),''),
            'Unavailable Ministry') AS name,
        CASE WHEN p.canonical::jsonb->'catalog_present'='true'::jsonb
            THEN coalesce(a.active,true) ELSE NULL END AS active
    FROM source x
    CROSS JOIN LATERAL jsonb_array_elements_text(x.values->'ministry_duids') n(duid)
    LEFT JOIN stewardship_snapshot_ministry m
        ON m.snapshot_id=x.source_id AND m.source_key=n.duid
    LEFT JOIN stewardship_source_ministry p ON p.id=m.payload_id
    LEFT JOIN stewardship_ministry_activity a
        ON a.configuration_id=x.configuration_id
        AND a.organization_id=x.organization_id AND a.ministry_duid=n.duid::integer
    WHERE (%(operational)s OR n.duid::integer=ANY(%(scope)s::integer[]))
      AND (%(ministry)s::integer IS NULL OR n.duid::integer=%(ministry)s)
), requests AS MATERIALIZED (
    SELECT r.id,r.entity_kind,r.entity_key,r.ministry_duid,r.action,r.state,
        r.outcome,s.submitted_at,s.family_version,f.family_duid,s.id AS submission_id,
        row_number() OVER (PARTITION BY s.family_id,r.entity_kind,r.entity_key,
            r.ministry_duid ORDER BY s.family_version DESC,r.id) AS revision
    FROM source x
    JOIN stewardship_submission s ON s.campaign_id=x.id AND s.mode='live'
    JOIN stewardship_ministry_request r ON r.submission_id=s.id
    JOIN ministries m ON m.duid=r.ministry_duid
    JOIN stewardship_family_campaign f ON f.id=s.family_id
), summary AS MATERIALIZED (
    SELECT m.*,
        count(r.id) FILTER (WHERE r.action='join') AS joining,
        count(r.id) FILTER (WHERE r.action='leave') AS leaving,
        count(r.id) FILTER (
            WHERE r.state IN ('new','assigned','in_progress')) AS unresolved,
        count(r.id) FILTER (
            WHERE r.state IN ('resolved','closed_no_response')) AS completed,
        count(r.id) AS requests
    FROM ministries m LEFT JOIN requests r ON r.ministry_duid=m.duid
        AND r.revision=1 AND r.state NOT IN ('cancelled','superseded')
    GROUP BY m.duid,m.name,m.active
), summary_filtered AS MATERIALIZED (
    SELECT * FROM summary WHERE
        (%(activity)s='any' OR (%(activity)s='active' AND active IS TRUE)
            OR (%(activity)s='inactive' AND active IS FALSE)
            OR (%(activity)s='unavailable' AND active IS NULL))
        AND (%(ministry)s::integer IS NOT NULL OR %(search)s=''
            OR position(lower(%(search)s) IN lower(name))>0
            OR position(%(search)s IN duid::text)>0)
), named AS MATERIALIZED (
    SELECT r.*,m.name AS ministry_name,p.canonical::jsonb AS person,
        CASE WHEN r.entity_kind='proposed_member'
            THEN s.answers->'proposed_members'->r.entity_key ELSE NULL END AS proposed,
        coalesce(nullif(btrim(concat_ws(' ',
            CASE WHEN r.entity_kind='member' THEN p.canonical::jsonb->>'firstName'
                ELSE s.answers->'proposed_members'->r.entity_key->>'first_name' END,
            CASE WHEN r.entity_kind='member' THEN p.canonical::jsonb->>'middleName'
                ELSE s.answers->'proposed_members'->r.entity_key->>'middle_name' END,
            CASE WHEN r.entity_kind='member' THEN p.canonical::jsonb->>'lastName'
                ELSE s.answers->'proposed_members'->r.entity_key->>'last_name'
                END)),''),
            'Unavailable Member') AS member_name
    FROM requests r JOIN summary_filtered m ON m.duid=r.ministry_duid
    CROSS JOIN source x
    JOIN stewardship_submission s ON s.id=r.submission_id
    LEFT JOIN stewardship_snapshot_member sm
        ON r.entity_kind='member' AND sm.snapshot_id=x.source_id
        AND sm.source_key=r.entity_key
    LEFT JOIN stewardship_source_member p ON p.id=sm.payload_id
        AND p.family_key=r.family_duid::text
    WHERE %(ministry)s::integer IS NOT NULL AND r.action=%(action)s
        AND (%(history)s OR (r.revision=1
            AND r.state NOT IN ('cancelled','superseded')))
        AND (%(state)s='any' OR r.state=%(state)s
            OR (%(state)s='unresolved' AND r.state IN ('new','assigned','in_progress')))
        AND (%(start)s='' OR (r.submitted_at AT TIME ZONE x.timezone)::date
            >=nullif(%(start)s,'')::date)
        AND (%(end)s='' OR (r.submitted_at AT TIME ZONE x.timezone)::date
            <=nullif(%(end)s,'')::date)
), filtered AS MATERIALIZED (
    SELECT * FROM named WHERE %(search)s=''
        OR position(lower(%(search)s) IN lower(member_name))>0
        OR (entity_kind='member' AND position(%(search)s IN entity_key)>0)
), page AS MATERIALIZED (
    SELECT *,row_number() OVER (ORDER BY
        CASE WHEN %(sort)s='name' THEN lower(member_name) END,
        CASE WHEN %(sort)s='name_desc' THEN lower(member_name) END DESC,
        CASE WHEN %(sort)s='newest' THEN submitted_at END DESC,
        CASE WHEN %(sort)s='oldest' THEN submitted_at END,id) AS ordinal
    FROM filtered ORDER BY ordinal
    LIMIT %(limit)s OFFSET %(offset)s
), detail AS (
    SELECT r.ordinal,r.id,r.ministry_duid,r.member_name,r.entity_kind,
        CASE WHEN r.entity_kind='member' THEN r.entity_key::bigint END AS member_duid,
        CASE WHEN r.entity_kind='proposed_member' THEN r.entity_key END AS proposed_id,
        r.action,r.state,r.outcome,r.submitted_at,
        r.revision=1 AS latest,
        CASE WHEN r.action='leave' THEN (
            SELECT string_agg(DISTINCT p.canonical::jsonb->>'ministryRoleName',', '
                ORDER BY p.canonical::jsonb->>'ministryRoleName')
            FROM stewardship_snapshot_roster m
            JOIN stewardship_source_roster p ON p.id=m.payload_id
            WHERE m.snapshot_id=x.source_id AND r.person IS NOT NULL
                AND p.canonical::jsonb->>'member_key'=r.entity_key
                AND p.canonical::jsonb->>'ministry_key'=r.ministry_duid::text
                AND p.canonical::jsonb->'current'='true'::jsonb
        ) END AS current_role,
        CASE WHEN r.action='join' THEN coalesce(
            r.person->>'sex',r.proposed->>'gender') END AS gender,
        CASE WHEN r.action='join' AND
            pg_input_is_valid(coalesce(r.person->>'birthdate',r.proposed->>'birth_date'),'date')
            THEN CASE WHEN coalesce(r.person->>'birthdate',
                r.proposed->>'birth_date')::date<=x.report_date
                THEN extract(year FROM age(x.report_date,
                    coalesce(r.person->>'birthdate',r.proposed->>'birth_date')::date))::integer
                END END AS age,
        CASE WHEN r.action='join' THEN
            CASE WHEN NOT %(operational)s AND coalesce(
                c.value->'publish_email','false'::jsonb)<>'true'::jsonb
                THEN 'not_published' ELSE 'available' END END AS email_visibility,
        CASE WHEN r.action='join' AND (%(operational)s
            OR c.value->'publish_email'='true'::jsonb)
            THEN CASE WHEN r.entity_kind='member' THEN c.value->'emails'
                ELSE jsonb_build_array(jsonb_build_object(
                    'value',r.proposed->>'email')) END END AS emails,
        CASE WHEN r.action='join' THEN
            CASE WHEN NOT %(operational)s AND coalesce(
                c.value->'publish_phone','false'::jsonb)<>'true'::jsonb
                THEN 'not_published' ELSE 'available' END END AS phone_visibility,
        CASE WHEN r.action='join' AND (%(operational)s
            OR c.value->'publish_phone'='true'::jsonb)
            THEN CASE WHEN r.entity_kind='member' THEN c.value->'phones'
                ELSE jsonb_build_object('home',r.proposed->>'home_phone',
                    'mobile',r.proposed->>'mobile_phone',
                    'work',r.proposed->>'work_phone') END END AS phones,
        a.fields AS address
    FROM page r CROSS JOIN source x
    LEFT JOIN LATERAL (
        SELECT p.canonical::jsonb AS value FROM stewardship_snapshot_contact m
        JOIN stewardship_source_contact p ON p.id=m.payload_id
        WHERE r.action='join' AND r.person IS NOT NULL AND m.snapshot_id=x.source_id
            AND m.source_key='member:'||r.entity_key
    ) c ON true
    LEFT JOIN LATERAL (
        SELECT p.canonical::jsonb->'fields' AS fields
        FROM stewardship_snapshot_address m
        JOIN stewardship_source_address p ON p.id=m.payload_id
        WHERE r.action='join' AND (r.person IS NOT NULL OR r.proposed IS NOT NULL)
            AND m.snapshot_id=x.source_id
            AND m.source_key='family:'||r.family_duid::text||':primary'
    ) a ON true
), summary_page AS (
    SELECT * FROM summary_filtered ORDER BY
        CASE WHEN %(sort)s='name_desc' THEN lower(name) END DESC,lower(name),duid
    LIMIT %(limit)s OFFSET CASE WHEN %(ministry)s::integer IS NULL
        THEN %(offset)s ELSE 0 END
)
SELECT CASE WHEN NOT z.values->'modules' ? 'ministry'
    THEN jsonb_build_object('disabled',true)
    WHEN x.id IS NULL THEN jsonb_build_object('unavailable',true)
    ELSE jsonb_build_object(
    'authorized',EXISTS(SELECT 1 FROM ministries),
    'metadata',jsonb_build_object('id',x.id,'name',x.name,'timezone',x.timezone,
        'source_id',x.source_id,'source_generation',x.source_generation,
        'source_as_of',x.source_as_of,'observed_at',x.observed_at,
        'report_date',x.report_date),
    'total',CASE WHEN %(ministry)s::integer IS NULL
        THEN (SELECT count(*) FROM summary_filtered)
        ELSE (SELECT count(*) FROM filtered) END,
    'summaries',coalesce((SELECT jsonb_agg(to_jsonb(p) ORDER BY
        CASE WHEN %(sort)s='name_desc' THEN lower(name) END DESC,lower(name),duid)
        FROM summary_page p),'[]'::jsonb),
    'rows',coalesce((SELECT jsonb_agg(to_jsonb(d)-'ordinal' ORDER BY ordinal)
        FROM detail d),'[]'::jsonb)) END::text
FROM selected z LEFT JOIN source x ON true
"""
