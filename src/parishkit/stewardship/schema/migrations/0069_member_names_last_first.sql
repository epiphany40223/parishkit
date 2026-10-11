-- Frozen forward migration file 0069 (the repository-wide file sequence):
-- the Ministry follow-up queue names each Member "Last, first" (#932). This
-- file is installed by its Django migration in the reports app and must
-- never change once released; tests/stewardship/test_schema_migration_files.py
-- pins its digest and checks that its copy of the replaced function equals
-- the fresh-install baseline's (schema/ministry_followup.sql). A fresh
-- install runs the baseline, the earlier files and then this; the baseline
-- already carries the replaced body, so the install ends in the same catalog
-- as an upgraded database.
--
-- The UX conventions ("Family and Member names") show a Member's own name
-- "Last, first" ("Smith, Ann"), so a column of Member names sorts by
-- surname. The queue's selection built "first last" and sorted by it.
--
-- Replaced function, copied whole from its latest definition (the baseline;
-- no earlier frozen file replaced it; 0040's stewardship_ministry_followup_v2
-- calls it unchanged) with only these changes:
--   stewardship_ministry_followup_v1   member_name is "Last, first", each
--                                      part trimmed by migration 0045's
--                                      stewardship_name_strip_v1, so the
--                                      Member sort orders by surname; the
--                                      search matches that name or the
--                                      "first last" form it replaces
-- Unchanged: the "Unavailable Member" fallback, every filter, the other
-- orders and their tiebreaks, and the returned keys. The function keeps its
-- signature, attributes and grants: it is not SECURITY DEFINER, before or
-- after, so there is no ALTER FUNCTION to repeat, and the DO block below
-- checks prosecdef, the settings and the ACL recorded before the
-- replacement. The Ministry report's own Member names are not changed here.
-- Reversing needs its own forward migration.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

-- The replaced function's grants before the replacement (with how many
-- definitions there are), compared in the DO block. A transaction-local
-- setting holds them, not a temporary table: the migration login has no TEMP
-- privilege.
SELECT set_config('stewardship.migration_0069_acl', (
    SELECT count(*)||':'||coalesce(string_agg(coalesce(p.proacl::text,'default'),';'),'')
    FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
    WHERE n.nspname='public' AND p.proname='stewardship_ministry_followup_v1'), true);

-- stewardship_ministry_followup_v1: the baseline body (ministry_followup.sql).
CREATE OR REPLACE FUNCTION public.stewardship_ministry_followup_v1(
    campaign_uuid uuid, filters jsonb, operational boolean, ministry_scope bigint[],
    viewer uuid, request_uuid uuid DEFAULT NULL,
    page_limit integer DEFAULT NULL, page_offset integer DEFAULT 0
) RETURNS jsonb LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
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
    WHERE c.id=campaign_uuid
), source AS MATERIALIZED (
    SELECT x.*,s.promoted_at AS source_as_of,statement_timestamp() AS observed_at
    FROM selected x JOIN stewardship_source_snapshot s ON s.id=x.source_id
    WHERE s.state='promoted' AND s.compacted_at IS NULL
        AND x.values->'modules' ? 'ministry'
), ministries AS MATERIALIZED (
    -- Widen before filtering, as the Ministry report does, so an unsupported
    -- configured ID cannot become an outage through predicate reordering.
    SELECT n.duid::bigint AS duid,
        coalesce(nullif(btrim(p.canonical::jsonb->>'name'),''),
            'Unavailable Ministry') AS name,
        n.in_campaign
    FROM source x
    -- The campaign's selections plus any Ministry it no longer selects that
    -- still has a current request (not cancelled or replaced) from this
    -- campaign (#342): removing a Ministry never hides answers already given;
    -- they are marked not in_campaign.
    CROSS JOIN LATERAL (
        SELECT n.duid,true FROM jsonb_array_elements_text(x.values->'ministry_duids') n(duid)
        UNION
        SELECT DISTINCT r.ministry_duid::text,false
        FROM stewardship_submission s
        JOIN stewardship_ministry_request r ON r.submission_id=s.id
        WHERE s.campaign_id=x.id AND s.mode='live'
          AND r.state NOT IN ('cancelled','superseded')
          AND NOT x.values->'ministry_duids' @> to_jsonb(r.ministry_duid)
    ) n(duid,in_campaign)
    LEFT JOIN stewardship_snapshot_ministry m
        ON m.snapshot_id=x.source_id AND m.source_key=n.duid
    LEFT JOIN stewardship_source_ministry p ON p.id=m.payload_id
    WHERE n.duid::bigint BETWEEN 1 AND 2147483647
      AND (operational OR n.duid::bigint=ANY(ministry_scope::bigint[]))
), requests AS MATERIALIZED (
    SELECT r.*,s.submitted_at,f.family_duid,m.name AS ministry_name,m.in_campaign,
        row_number() OVER (PARTITION BY s.family_id,r.entity_kind,r.entity_key,
            r.ministry_duid ORDER BY s.family_version DESC,r.id) AS revision
    FROM source x
    JOIN stewardship_submission s ON s.campaign_id=x.id AND s.mode='live'
    JOIN stewardship_ministry_request r ON r.submission_id=s.id
    JOIN ministries m ON m.duid=r.ministry_duid
    JOIN stewardship_family_campaign f ON f.id=s.family_id
), named AS MATERIALIZED (
    -- A Member is named "Last, first" (UX conventions, "Family and Member
    -- names", #932), so the name sort orders by surname; the search also
    -- matches the given-name-first form shown before.
    SELECT r.*,coalesce(nullif(concat_ws(', ',
            nullif(stewardship_name_strip_v1(n.last_name),''),
            nullif(stewardship_name_strip_v1(n.first_name),'')),''),
            'Unavailable Member') AS member_name,
        coalesce(nullif(btrim(concat_ws(' ',n.first_name,n.last_name)),''),
            'Unavailable Member') AS member_search_name,
        -- "Cannot participate in any ministries" (#247) explains a leave.
        coalesce(s.answers#>ARRAY['service',
            CASE WHEN r.entity_kind='member' THEN 'members' ELSE 'proposed_members' END,
            r.entity_key,'cannot_serve']='true'::jsonb,false) AS cannot_serve
    FROM requests r CROSS JOIN source x
    JOIN stewardship_submission s ON s.id=r.submission_id
    LEFT JOIN stewardship_snapshot_member sm
        ON r.entity_kind='member' AND sm.snapshot_id=x.source_id
        AND sm.source_key=r.entity_key
    LEFT JOIN stewardship_source_member p ON p.id=sm.payload_id
        AND p.family_key=r.family_duid::text
    CROSS JOIN LATERAL (SELECT
        CASE WHEN r.entity_kind='member' THEN p.canonical::jsonb->>'firstName'
            ELSE s.answers->'proposed_members'->r.entity_key->>'first_name' END,
        CASE WHEN r.entity_kind='member' THEN p.canonical::jsonb->>'lastName'
            ELSE s.answers->'proposed_members'->r.entity_key->>'last_name' END
    ) n(first_name,last_name)
    WHERE CASE WHEN request_uuid IS NOT NULL THEN r.id=request_uuid ELSE
        ((filters->>'history')='all' OR (r.revision=1
            AND r.state NOT IN ('cancelled','superseded')))
        AND ((filters->>'ministry')='' OR r.ministry_duid::text=(filters->>'ministry'))
        AND ((filters->>'action')='any' OR r.action=(filters->>'action'))
        AND ((filters->>'state')='any' OR r.state=(filters->>'state')
            OR ((filters->>'state')='unresolved'
                AND r.state IN ('new','assigned','in_progress')))
        AND ((filters->>'outcome')='any' OR r.outcome=(filters->>'outcome'))
        AND ((filters->>'assignee')='any'
            OR ((filters->>'assignee')='mine' AND r.assignee_id=viewer)
            OR ((filters->>'assignee')='unassigned' AND r.assignee_id IS NULL)
            OR r.assignee_id::text=(filters->>'assignee')) END
), filtered AS MATERIALIZED (
    SELECT * FROM named WHERE request_uuid IS NOT NULL OR (filters->>'search')=''
        OR position(lower((filters->>'search')) IN lower(member_name))>0
        OR position(lower((filters->>'search')) IN lower(member_search_name))>0
        OR position(lower((filters->>'search')) IN lower(ministry_name))>0
), page AS MATERIALIZED (
    SELECT *,row_number() OVER (ORDER BY
        CASE WHEN (filters->>'sort')='name' THEN lower(member_name) END,
        CASE WHEN (filters->>'sort')='ministry' THEN lower(ministry_name) END,
        CASE WHEN (filters->>'sort')='oldest' THEN submitted_at END,
        submitted_at DESC,id) AS ordinal
    FROM filtered ORDER BY ordinal LIMIT page_limit OFFSET page_offset
), detail AS (
    SELECT r.ordinal,r.id,r.version,r.ministry_duid,r.ministry_name,r.in_campaign,r.member_name,
        r.entity_kind,r.action,r.state,r.outcome,r.assignee_id,r.submitted_at,
        r.cannot_serve,
        r.resolved_at,r.resolution_source_id IS NOT NULL AS source_resolved,
        r.revision=1 AS latest,
        r.state IN ('new','assigned','in_progress') AS open,
        h.notes,h.email_contact_at,h.phone_contact_at,h.last_contact_at
    FROM page r LEFT JOIN LATERAL (
        SELECT (array_agg(v.notes ORDER BY c.depth,v.expected_version DESC))[1] AS notes,
            max(v.contact_at) FILTER (WHERE v.contact_channel='email') AS email_contact_at,
            max(v.contact_at) FILTER (WHERE v.contact_channel='phone') AS phone_contact_at,
            max(v.contact_at) AS last_contact_at
        FROM stewardship_ministry_workflow_chain_v1(r.id) c
        JOIN stewardship_ministry_revision v ON v.request_id=c.request_id
    ) h ON true
)
SELECT CASE WHEN NOT z.values->'modules' ? 'ministry'
    THEN jsonb_build_object('disabled',true)
    WHEN x.id IS NULL THEN jsonb_build_object('unavailable',true)
    ELSE jsonb_build_object(
    'authorized',EXISTS(SELECT 1 FROM ministries),
    'metadata',jsonb_build_object('id',x.id,'name',x.name,'timezone',x.timezone,
        'source_as_of',x.source_as_of,'observed_at',x.observed_at),
    'ministries',coalesce((SELECT jsonb_agg(jsonb_build_object('duid',duid,'name',name,'in_campaign',in_campaign)
        ORDER BY lower(name),duid) FROM ministries),'[]'::jsonb),
    'total',(SELECT count(*) FROM filtered),
    'rows',coalesce((SELECT jsonb_agg(to_jsonb(d)-'ordinal' ORDER BY ordinal)
        FROM detail d),'[]'::jsonb)) END
FROM selected z LEFT JOIN source x ON true;
$$;

DO $check$
BEGIN
    -- One definition, with its declared signature and attributes, building
    -- the "Last, first" name and searching both forms.
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname='stewardship_ministry_followup_v1'
          AND pg_get_function_identity_arguments(p.oid)=
              'campaign_uuid uuid, filters jsonb, operational boolean, '
              'ministry_scope bigint[], viewer uuid, request_uuid uuid, '
              'page_limit integer, page_offset integer'
          AND NOT p.prosecdef AND p.provolatile='s'
          AND p.prorettype='jsonb'::regtype
          AND p.proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
          AND p.prosrc LIKE '%concat_ws('', '',%stewardship_name_strip_v1(n.last_name)%'
          AND p.prosrc LIKE '%IN lower(member_search_name)%')<>1 THEN
        RAISE EXCEPTION 'Migration 0069 (Member names Last, first) is not installed as declared';
    END IF;
    -- CREATE OR REPLACE keeps the grants; check that nothing changed them.
    IF (SELECT count(*)||':'||coalesce(string_agg(coalesce(p.proacl::text,'default'),';'),'')
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname='stewardship_ministry_followup_v1')
        IS DISTINCT FROM current_setting('stewardship.migration_0069_acl') THEN
        RAISE EXCEPTION 'Migration 0069 changed the Ministry follow-up selection''s grants';
    END IF;
END
$check$;
