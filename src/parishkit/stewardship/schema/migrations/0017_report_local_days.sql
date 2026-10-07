-- Frozen forward migration file 0017 (the repository-wide file sequence;
-- Django's stewardship_reports.0003): report date filters in the viewer's
-- browser time zone (#558). This file is installed by
-- reports/migrations/0003_report_local_days.py and must never change once
-- released; tests/stewardship/test_schema_migration_files.py pins its digest.
-- A later change gets its own numbered file. A fresh install runs the
-- baseline, 0002 to 0016 and then this, and ends in the same catalog as an
-- upgraded database.
--
-- The Additional information and Ministry report pages, their exports and
-- the Ministry packet filter requests by the day they were submitted. Until
-- now those days were the campaign's time zone, computed inside the report
-- functions. Every date an Admin page takes is now in the viewer's browser
-- zone (the Administrator, 2026-10-04), so the filters carry that zone as a
-- new 'zone' key, and a day runs from local midnight to the next local
-- midnight there.
--
-- The v1 functions are frozen (their closed filter keys, and the campaign-zone
-- days they compute), so this file adds v2 copies that differ only in the
-- date predicate and the 'zone' key, and capture triggers that call v2 for
-- zoned filters. Everything in the copies is otherwise byte for byte the
-- baseline v1 text. The v1 functions stay installed and the new capture
-- functions still accept filters without 'zone' (passing them to v1), so the
-- previous release keeps working against this schema if it is rolled back.
-- Like v1, none of these functions is SECURITY DEFINER: each runs with its
-- caller's grants and a fixed search_path, and the reports keep jit off.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

CREATE FUNCTION public.stewardship_information_report_v2(
    campaign uuid, parameters jsonb, page_number integer DEFAULT NULL,
    item_uuid uuid DEFAULT NULL, page_size integer DEFAULT 50
) RETURNS jsonb LANGUAGE plpgsql STABLE
-- JIT compilation costs seconds per call at parish size and saves nothing
-- here, as in the directory report. search_path stays first so the proconfig
-- order matches an ALTER FUNCTION ... SET jit TO off on an installed database.
SET search_path TO pg_catalog,public,pg_temp
SET jit TO off AS $$
DECLARE f jsonb:=parameters->'filters'; answer jsonb;
BEGIN
    IF jsonb_typeof(parameters) IS DISTINCT FROM 'object'
       OR NOT parameters ?& ARRAY['filters','history']
       OR parameters-ARRAY['filters','history']<>'{}'::jsonb
       OR jsonb_typeof(parameters->'history') IS DISTINCT FROM 'boolean'
       OR jsonb_typeof(f) IS DISTINCT FROM 'object'
       OR NOT f ?& ARRAY['search','disposition','needed','completed','start','end','sort','zone']
       OR f-ARRAY['search','disposition','needed','completed','start','end','sort','zone']<>'{}'::jsonb
       OR EXISTS(SELECT 1 FROM jsonb_each(f) WHERE jsonb_typeof(value)<>'string')
       OR length(f->>'search')>200
       OR f->>'disposition' NOT IN ('current_actionable','superseded','withdrawn','all')
       OR f->>'needed' NOT IN ('any','yes','no')
       OR f->>'completed' NOT IN ('any','yes','no')
       OR f->>'sort' NOT IN ('newest','oldest','name','name_desc')
       OR (page_number IS NOT NULL AND page_number NOT BETWEEN 1 AND 10000)
       OR page_size IS NULL OR page_size NOT BETWEEN 1 AND 100
    THEN RAISE EXCEPTION 'Invalid information report parameters' USING ERRCODE='23514'; END IF;
    -- Python validates the same canonical dates before reaching SQL. The SQL
    -- owner also rejects alternate spellings on direct restricted-role writes.
    IF (f->>'start'<>'' AND (f->>'start')::date::text<>f->>'start')
       OR (f->>'end'<>'' AND (f->>'end')::date::text<>f->>'end')
       OR (f->>'start'<>'' AND f->>'end'<>'' AND f->>'start'>f->>'end')
       -- Days are the viewer's browser zone (#558): required with a date,
       -- and always one the server's time zone catalog knows.
       OR ((f->>'start'<>'' OR f->>'end'<>'') AND f->>'zone'='')
       OR (f->>'zone'<>'' AND NOT EXISTS(SELECT 1 FROM pg_timezone_names WHERE name=stewardship_timezone_name_v1(f->>'zone')))
    THEN RAISE EXCEPTION 'Invalid information report interval' USING ERRCODE='23514'; END IF;

    WITH selected AS MATERIALIZED (
        SELECT c.id,cc.name,cc.timezone,cc.id AS timezone_configuration_id,
            sc.snapshot_id AS source_id,ss.generation AS source_generation,
            ss.promoted_at AS source_as_of,statement_timestamp() AS observed_at
        FROM stewardship_campaign c
        JOIN stewardship_campaign_configuration cc ON cc.id=c.active_configuration_id
        JOIN stewardship_source_current sc ON sc.singleton
        JOIN stewardship_source_snapshot ss ON ss.id=sc.snapshot_id
            AND ss.state='promoted' AND ss.compacted_at IS NULL
        WHERE c.id=campaign
    ), history AS MATERIALIZED (
        SELECT stewardship_weekly_history_v1(id,'production',NULL) AS value FROM selected
    ), rows AS MATERIALIZED (
        SELECT i.id,i.version,i.text,i.disposition,i.replacement_id,
            i.follow_up_needed,i.followed_up_at,r.followed_up_by_id,
            coalesce(u.email,'Former portal user') AS completed_by,
            coalesce(r.notes,'') AS notes,s.submitted_at,fam.family_duid,
            coalesce(nullif(btrim(p.canonical::jsonb->>'lastName'),''),
                nullif(btrim(p.canonical::jsonb->>'mailingName'),''),
                nullif(btrim(concat_ws(' ',
                    nullif(btrim(p.canonical::jsonb->>'firstName'),''),
                    nullif(btrim(p.canonical::jsonb->>'lastName'),''))),''),
                'Family') AS family_name,
            h.value->'reported' ? i.id::text AS previously_reported,
            h.value->'corrected' @>
                jsonb_build_array(jsonb_build_array(i.id::text,i.disposition))
                AS correction_resolved
        FROM selected x CROSS JOIN history h
        JOIN stewardship_submission s ON s.campaign_id=x.id AND s.mode='live'
        JOIN stewardship_additional_information i ON i.submission_id=s.id
        JOIN stewardship_family_campaign fam ON fam.id=s.family_id
        LEFT JOIN stewardship_snapshot_family m
            ON m.snapshot_id=x.source_id AND m.source_key=fam.family_duid::text
        LEFT JOIN stewardship_source_family p ON p.id=m.payload_id
        LEFT JOIN LATERAL (
            SELECT notes,followed_up_by_id FROM stewardship_information_revision
            WHERE item_id=i.id ORDER BY expected_version DESC LIMIT 1) r ON true
        LEFT JOIN stewardship_portal_user u ON u.id=r.followed_up_by_id
        WHERE (item_uuid IS NULL OR i.id=item_uuid)
          -- From local midnight of the first day to local midnight after the
          -- last, in the viewer's zone; a DST day is 23 or 25 hours long.
          AND (f->>'start'='' OR s.submitted_at>=((f->>'start')::date::timestamp
              AT TIME ZONE stewardship_timezone_name_v1(f->>'zone')))
          AND (f->>'end'='' OR s.submitted_at<(((f->>'end')::date+1)::timestamp
              AT TIME ZONE stewardship_timezone_name_v1(f->>'zone')))
    ), filtered AS MATERIALIZED (
        SELECT * FROM rows WHERE (f->>'disposition'='all' OR disposition=f->>'disposition')
          AND (f->>'needed'='any' OR follow_up_needed=(f->>'needed'='yes'))
          AND (f->>'completed'='any' OR (followed_up_at IS NOT NULL)=(f->>'completed'='yes'))
          AND (f->>'search'='' OR position(lower(f->>'search') IN lower(family_name))>0
            OR position(f->>'search' IN family_duid::text)>0
            OR position(lower(f->>'search') IN lower(text))>0
            OR position(lower(f->>'search') IN lower(notes))>0)
    ), ordered AS (
        SELECT *,row_number() OVER (ORDER BY
            CASE WHEN f->>'sort'='newest' THEN submitted_at END DESC,
            CASE WHEN f->>'sort'='oldest' THEN submitted_at END,
            CASE WHEN f->>'sort'='name' THEN lower(family_name) END,
            CASE WHEN f->>'sort'='name_desc' THEN lower(family_name) END DESC,id) AS ordinal
        FROM filtered
    ), page AS (
        SELECT * FROM ordered ORDER BY ordinal
        LIMIT CASE WHEN page_number IS NULL THEN NULL ELSE page_size END
        OFFSET CASE WHEN page_number IS NULL THEN 0 ELSE (page_number-1)*page_size END
    ), detached AS (
        SELECT ordinal,to_jsonb(page)-'ordinal' || jsonb_build_object('history',
            CASE WHEN (parameters->>'history')::boolean THEN coalesce((
                SELECT jsonb_agg(jsonb_build_object(
                    'version',r.expected_version+1,'created_at',r.created_at,
                    'actor_id',r.actor_id,'actor_label',coalesce(a.email,'Former portal user'),
                    'follow_up_needed',r.follow_up_needed,'followed_up',r.followed_up,
                    'followed_up_at',r.followed_up_at,'followed_up_by_id',r.followed_up_by_id,
                    'completed_by',coalesce(u.email,'Former portal user'),'notes',r.notes)
                    ORDER BY r.expected_version)
                FROM stewardship_information_revision r
                LEFT JOIN stewardship_portal_user a ON a.id=r.actor_id
                LEFT JOIN stewardship_portal_user u ON u.id=r.followed_up_by_id
                WHERE r.item_id=page.id AND r.expected_version<page.version
            ),'[]'::jsonb) ELSE '[]'::jsonb END) AS value FROM page
    )
    SELECT jsonb_build_object('metadata',to_jsonb(x),
        'total',(SELECT count(*) FROM filtered),
        'rows',coalesce((SELECT jsonb_agg(value ORDER BY ordinal) FROM detached),'[]'::jsonb))
    INTO answer FROM selected x;
    RETURN answer;
END $$;

CREATE FUNCTION public.stewardship_ministry_report_v2(
    campaign_uuid uuid, filters jsonb, operational boolean, ministry_scope bigint[],
    ministry_id integer DEFAULT NULL, request_action text DEFAULT 'join',
    page_limit integer DEFAULT NULL, page_offset integer DEFAULT 0
) RETURNS jsonb LANGUAGE sql STABLE
-- JIT compilation costs seconds per call at parish size and saves nothing
-- here, as in the directory report. search_path stays first so the proconfig
-- order matches an ALTER FUNCTION ... SET jit TO off on an installed database.
SET search_path TO pg_catalog,public,pg_temp
SET jit TO off AS $$
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
    SELECT x.*,s.organization_id,s.generation AS source_generation,
        s.promoted_at AS source_as_of,statement_timestamp() AS observed_at,
        (statement_timestamp() AT TIME ZONE stewardship_timezone_name_v1(x.timezone))::date AS report_date
    FROM selected x JOIN stewardship_source_snapshot s ON s.id=x.source_id
    WHERE s.state='promoted' AND s.compacted_at IS NULL
        AND x.values->'modules' ? 'ministry'
), ministries AS MATERIALIZED (
    -- Configuration permits signed-64-bit IDs; source/requests accept only
    -- positive signed-32-bit DUIDs. Widen casts before filtering so planner
    -- predicate reordering cannot turn an unsupported draft ID into an outage.
    SELECT n.duid::bigint AS duid,
        coalesce(nullif(btrim(p.canonical::jsonb->>'name'),''),
            'Unavailable Ministry') AS name,
        CASE WHEN p.canonical::jsonb->'catalog_present'='true'::jsonb
            THEN coalesce(a.active,true) ELSE NULL END AS active,
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
    LEFT JOIN stewardship_ministry_activity a
        ON a.configuration_id=x.configuration_id
        AND a.organization_id=x.organization_id AND a.ministry_duid=n.duid::bigint
    WHERE n.duid::bigint BETWEEN 1 AND 2147483647
      AND (operational OR n.duid::bigint=ANY(ministry_scope::bigint[]))
      AND (ministry_id::integer IS NULL OR n.duid::bigint=ministry_id)
), requests AS MATERIALIZED (
    SELECT r.id,r.entity_kind,r.entity_key,r.ministry_duid,r.action,r.state,
        r.outcome,r.assignee_id,s.submitted_at,s.family_version,f.family_duid,s.id AS submission_id,
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
    GROUP BY m.duid,m.name,m.active,m.in_campaign
), summary_filtered AS MATERIALIZED (
    SELECT * FROM summary WHERE
        ((filters->>'activity')='any' OR ((filters->>'activity')='active' AND active IS TRUE)
            OR ((filters->>'activity')='inactive' AND active IS FALSE)
            OR ((filters->>'activity')='unavailable' AND active IS NULL))
        AND (ministry_id::integer IS NOT NULL OR (filters->>'search')=''
            OR position(lower((filters->>'search')) IN lower(name))>0
            OR position((filters->>'search') IN duid::text)>0)
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
    WHERE ministry_id::integer IS NOT NULL AND r.action=request_action
        AND ((filters->>'history'='all') OR (r.revision=1
            AND r.state NOT IN ('cancelled','superseded')))
        AND ((filters->>'state')='any' OR r.state=(filters->>'state')
            OR ((filters->>'state')='unresolved' AND r.state IN ('new','assigned','in_progress')))
        -- Days in the viewer's browser zone (#558), as in the information report.
        AND ((filters->>'start')='' OR r.submitted_at>=((filters->>'start')::date::timestamp
            AT TIME ZONE stewardship_timezone_name_v1(filters->>'zone')))
        AND ((filters->>'end')='' OR r.submitted_at<(((filters->>'end')::date+1)::timestamp
            AT TIME ZONE stewardship_timezone_name_v1(filters->>'zone')))
), filtered AS MATERIALIZED (
    SELECT * FROM named WHERE (filters->>'search')=''
        OR position(lower((filters->>'search')) IN lower(member_name))>0
        OR (entity_kind='member' AND position((filters->>'search') IN entity_key)>0)
), page AS MATERIALIZED (
    SELECT *,row_number() OVER (ORDER BY
        CASE WHEN (filters->>'sort')='name' THEN lower(member_name) END,
        CASE WHEN (filters->>'sort')='name_desc' THEN lower(member_name) END DESC,
        CASE WHEN (filters->>'sort')='newest' THEN submitted_at END DESC,
        CASE WHEN (filters->>'sort')='oldest' THEN submitted_at END,id) AS ordinal
    FROM filtered ORDER BY ordinal
    LIMIT page_limit OFFSET page_offset
), detail AS (
    SELECT r.ordinal,r.id,r.ministry_duid,r.member_name,r.entity_kind,
        CASE WHEN r.entity_kind='member' THEN r.entity_key::bigint END AS member_duid,
        CASE WHEN r.entity_kind='proposed_member' THEN r.entity_key END AS proposed_id,
        r.action,r.state,r.outcome,r.submitted_at,
        r.revision=1 AS latest,
        -- Resolved in this statement so an export captures who held the work
        -- as of its data, not whoever holds that identity when it renders.
        (SELECT u.email FROM stewardship_portal_user u
            WHERE u.id=r.assignee_id) AS assignee,
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
            CASE WHEN NOT operational AND coalesce(
                c.value->'publish_email','false'::jsonb)<>'true'::jsonb
                THEN 'not_published' ELSE 'available' END END AS email_visibility,
        CASE WHEN r.action='join' AND (operational
            OR c.value->'publish_email'='true'::jsonb)
            THEN CASE WHEN r.entity_kind='member' THEN c.value->'emails'
                ELSE jsonb_build_array(jsonb_build_object(
                    'value',r.proposed->>'email')) END END AS emails,
        CASE WHEN r.action='join' THEN
            CASE WHEN NOT operational AND coalesce(
                c.value->'publish_phone','false'::jsonb)<>'true'::jsonb
                THEN 'not_published' ELSE 'available' END END AS phone_visibility,
        CASE WHEN r.action='join' AND (operational
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
        CASE WHEN (filters->>'sort')='name_desc' THEN lower(name) END DESC,lower(name),duid
    LIMIT page_limit OFFSET CASE WHEN ministry_id::integer IS NULL
        THEN page_offset ELSE 0 END
)
SELECT CASE WHEN NOT z.values->'modules' ? 'ministry'
    THEN jsonb_build_object('disabled',true)
    WHEN x.id IS NULL THEN jsonb_build_object('unavailable',true)
    ELSE jsonb_build_object(
    'authorized',EXISTS(SELECT 1 FROM ministries),
    'authorization_scope',jsonb_build_object('capability','ministry_report',
        'operational',operational,'ministries',coalesce((SELECT jsonb_agg(duid ORDER BY duid)
            FROM ministries),'[]'::jsonb)),
    'metadata',jsonb_build_object('id',x.id,'name',x.name,'timezone',x.timezone,
        'source_id',x.source_id,'source_generation',x.source_generation,
        'source_as_of',x.source_as_of,'observed_at',x.observed_at,
        'report_date',x.report_date),
    'total',CASE WHEN ministry_id::integer IS NULL
        THEN (SELECT count(*) FROM summary_filtered)
        ELSE (SELECT count(*) FROM filtered) END,
    'summaries',coalesce((SELECT jsonb_agg(to_jsonb(p) ORDER BY
        CASE WHEN (filters->>'sort')='name_desc' THEN lower(name) END DESC,lower(name),duid)
        FROM summary_page p),'[]'::jsonb),
    'rows',coalesce((SELECT jsonb_agg(to_jsonb(d)-'ordinal' ORDER BY ordinal)
        FROM detail d),'[]'::jsonb)) END
FROM selected z LEFT JOIN source x ON true;
$$;

CREATE FUNCTION public.stewardship_information_export_capture_v2() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF TG_OP<>'INSERT' THEN
        RAISE EXCEPTION 'Information export snapshots are immutable' USING ERRCODE='23514';
    END IF;
    PERFORM stewardship_export_campaign_lock_v1(NEW.campaign_id,false);
    IF NOT stewardship_export_authorized_v1(NEW.actor_id)
       OR NOT stewardship_export_admitted_v1(NEW.campaign_id,true)
       OR current_user='pk_stewardship_worker'
       OR NOT EXISTS(SELECT 1 FROM stewardship_system_configuration
           WHERE active_configuration_id=NEW.configuration_id)
    THEN RAISE EXCEPTION 'Information export capture is unavailable' USING ERRCODE='23514'; END IF;
    NEW.created_at:=statement_timestamp();
    -- Filters with a zone are browser-local days (#558); without one they are
    -- the campaign-zone days of a release from before 0017 (a rollback).
    NEW.document:=CASE WHEN NEW.parameters->'filters' ? 'zone'
        THEN stewardship_information_report_v2(NEW.campaign_id,NEW.parameters)
        ELSE stewardship_information_report_v1(NEW.campaign_id,NEW.parameters) END;
    IF NEW.document IS NULL THEN
        RAISE EXCEPTION 'Information export inputs are unavailable' USING ERRCODE='23514';
    END IF;
    NEW.source_id:=(NEW.document->'metadata'->>'source_id')::uuid;
    NEW.row_count:=(NEW.document->>'total')::integer;
    RETURN NEW;
END $$;

CREATE FUNCTION public.stewardship_ministry_export_capture_v2() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE current_scope jsonb; filters jsonb; ministry_id integer; request_action text;
        selection bigint[]; packet boolean;
BEGIN
    IF TG_OP<>'INSERT' THEN
        RAISE EXCEPTION 'Ministry export snapshots are immutable' USING ERRCODE='23514';
    END IF;
    PERFORM stewardship_export_campaign_lock_v1(NEW.campaign_id,false);
    current_scope:=stewardship_ministry_scope_v1(NEW.actor_id);
    IF current_scope IS NULL
       OR NOT stewardship_export_admitted_v1(NEW.campaign_id,true)
       OR current_user='pk_stewardship_worker'
       OR NOT EXISTS(SELECT 1 FROM stewardship_system_configuration
           WHERE active_configuration_id=NEW.configuration_id)
    THEN RAISE EXCEPTION 'Ministry export capture is unavailable' USING ERRCODE='23514'; END IF;
    -- Closed parameters have identical meanings in HTML and immutable capture.
    filters:=NEW.parameters->'filters';
    request_action:=NEW.parameters->>'action';
    packet:=coalesce(request_action='packet',false);
    -- Only a packet carries a Ministry selection, and it always carries one.
    IF jsonb_typeof(NEW.parameters) IS DISTINCT FROM 'object'
       OR NOT NEW.parameters ?& ARRAY['filters','ministry','action']
       OR NEW.parameters-ARRAY['filters','ministry','action','ministries']<>'{}'::jsonb
       OR (NEW.parameters ? 'ministries') IS DISTINCT FROM packet
       OR jsonb_typeof(filters) IS DISTINCT FROM 'object'
       OR NOT filters ?& ARRAY['search','activity','history','state','start','end','sort']
       OR filters-ARRAY['search','activity','history','state','start','end','sort','zone']<>'{}'::jsonb
       OR EXISTS(SELECT 1 FROM jsonb_each(filters) f WHERE jsonb_typeof(f.value)<>'string')
       OR octet_length(filters->>'search')>131072
       OR filters->>'activity' NOT IN ('any','active','inactive','unavailable')
       OR filters->>'history' NOT IN ('current','all')
       OR filters->>'state' NOT IN ('any','unresolved','new','assigned','in_progress',
           'resolved','closed_no_response','cancelled','superseded')
       OR filters->>'sort' NOT IN ('name','name_desc','newest','oldest')
       OR request_action IS NULL
       OR request_action NOT IN ('summary','join','leave','packet')
    THEN RAISE EXCEPTION 'Invalid Ministry export parameters' USING ERRCODE='23514'; END IF;
    IF NEW.parameters->'ministry'<>'null'::jsonb THEN
        IF jsonb_typeof(NEW.parameters->'ministry')<>'number'
           OR NOT (NEW.parameters->>'ministry') ~ '^[1-9][0-9]{0,9}$'
           OR (NEW.parameters->>'ministry')::bigint>2147483647
        THEN RAISE EXCEPTION 'Invalid Ministry selection' USING ERRCODE='23514'; END IF;
        ministry_id:=(NEW.parameters->>'ministry')::integer;
    END IF;
    IF packet THEN
        -- A packet's only choices are its Ministries and the history option;
        -- every other filter must hold its neutral value so one capture has
        -- exactly one meaning. JSON null selects every authorized Ministry.
        -- A packet has no dates, so its zone (#558) is blank when sent.
        IF coalesce(filters->>'zone','')<>''
           OR filters-'history'-'zone'<>jsonb_build_object('search','','activity','any',
               'state','any','start','','end','','sort','name')
        THEN RAISE EXCEPTION 'Invalid Ministry selection' USING ERRCODE='23514'; END IF;
        IF NEW.parameters->'ministries'<>'null'::jsonb THEN
            IF jsonb_typeof(NEW.parameters->'ministries')<>'array'
               OR jsonb_array_length(NEW.parameters->'ministries') NOT BETWEEN 1 AND 200
               OR EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.parameters->'ministries') e
                   WHERE jsonb_typeof(e.value)<>'number'
                      OR NOT (e.value#>>'{}') ~ '^[1-9][0-9]{0,9}$'
                      OR (e.value#>>'{}')::bigint>2147483647)
            THEN RAISE EXCEPTION 'Invalid Ministry selection' USING ERRCODE='23514'; END IF;
            selection:=ARRAY(SELECT (e.value#>>'{}')::bigint
                FROM jsonb_array_elements(NEW.parameters->'ministries')
                    WITH ORDINALITY e(value,position) ORDER BY e.position);
            -- Canonical means strictly ascending, which also forbids repeats.
            IF selection<>ARRAY(SELECT DISTINCT v FROM unnest(selection) v ORDER BY v)
            THEN RAISE EXCEPTION 'Invalid Ministry selection' USING ERRCODE='23514'; END IF;
        END IF;
    END IF;
    IF (ministry_id IS NULL) IS DISTINCT FROM (request_action IN ('summary','packet'))
       OR (request_action='summary' AND (filters->>'history'<>'current'
           OR filters->>'state'<>'any' OR filters->>'start'<>'' OR filters->>'end'<>''
           OR filters->>'sort' NOT IN ('name','name_desc')))
       OR EXISTS(SELECT 1 FROM jsonb_each_text(filters) f
           WHERE f.key IN ('start','end') AND f.value<>'' AND (
               NOT f.value ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
               OR NOT pg_input_is_valid(f.value,'date')))
       OR (filters->>'start'<>'' AND filters->>'end'<>''
           AND filters->>'start'>filters->>'end')
       -- A zone (#558) is required with a date and always a known one.
       OR (filters ? 'zone' AND (filters->>'start'<>'' OR filters->>'end'<>'')
           AND filters->>'zone'='')
       OR (filters ? 'zone' AND filters->>'zone'<>''
           AND NOT EXISTS(SELECT 1 FROM pg_timezone_names WHERE name=stewardship_timezone_name_v1(filters->>'zone')))
    THEN RAISE EXCEPTION 'Invalid Ministry selection' USING ERRCODE='23514'; END IF;
    NEW.created_at:=statement_timestamp();
    IF packet THEN
        NEW.document:=stewardship_ministry_packet_v1(NEW.campaign_id,selection,
            filters->>'history'='all',(current_scope->>'operational')::boolean,
            ARRAY(SELECT value::bigint FROM jsonb_array_elements_text(current_scope->'ministries')));
        -- Scope is intersected, never trusted. An explicit selection must
        -- resolve to exactly the Ministries asked for: dropping one the caller
        -- cannot see would capture a packet that misstates its own request.
        IF selection IS NOT NULL AND NEW.document ? 'sections'
           AND jsonb_array_length(NEW.document->'sections')<>cardinality(selection)
        THEN RAISE EXCEPTION 'Ministry export inputs are unavailable' USING ERRCODE='23514'; END IF;
    ELSE
        -- Filters with a zone are browser-local days (#558); without one they
        -- are the campaign-zone days of a release from before 0017.
        NEW.document:=CASE WHEN filters ? 'zone'
            THEN stewardship_ministry_report_v2(NEW.campaign_id,filters,
            (current_scope->>'operational')::boolean,
            ARRAY(SELECT value::bigint FROM jsonb_array_elements_text(current_scope->'ministries')),
            ministry_id,request_action)
            ELSE stewardship_ministry_report_v1(NEW.campaign_id,filters,
            (current_scope->>'operational')::boolean,
            ARRAY(SELECT value::bigint FROM jsonb_array_elements_text(current_scope->'ministries')),
            ministry_id,request_action) END;
    END IF;
    IF NEW.document IS NULL OR NEW.document ?| ARRAY['disabled','unavailable']
       OR (NEW.document->'authorized'<>'true'::jsonb AND
           (ministry_id IS NOT NULL OR current_scope->'operational'<>'true'::jsonb))
    THEN RAISE EXCEPTION 'Ministry export inputs are unavailable' USING ERRCODE='23514'; END IF;
    -- Lifecycle authority intentionally covers the whole selected policy scope.
    -- Audit attribution instead names only Ministries present after filtering,
    -- including a named empty detail section but not an empty summary result.
    NEW.authorization_scope:=NEW.document->'authorization_scope' || jsonb_build_object(
        'result_ministries',coalesce((SELECT jsonb_agg((value->>'duid')::bigint
            ORDER BY (value->>'duid')::bigint)
            FROM jsonb_array_elements(NEW.document->
                CASE WHEN packet THEN 'sections' ELSE 'summaries' END)), '[]'::jsonb));
    NEW.source_id:=(NEW.document->'metadata'->>'source_id')::uuid;
    NEW.row_count:=(NEW.document->>'total')::integer;
    RETURN NEW;
END $$;

-- The export snapshots' capture triggers now call the v2 capture functions.
-- CREATE OR REPLACE TRIGGER keeps each trigger's name, timing and events.
CREATE OR REPLACE TRIGGER information_export_capture BEFORE INSERT OR UPDATE OR DELETE
    ON public.stewardship_information_export_snapshot FOR EACH ROW
    EXECUTE FUNCTION public.stewardship_information_export_capture_v2();
CREATE OR REPLACE TRIGGER ministry_export_capture BEFORE INSERT OR UPDATE OR DELETE
    ON public.stewardship_ministry_export_snapshot FOR EACH ROW
    EXECUTE FUNCTION public.stewardship_ministry_export_capture_v2();

-- Refuse to commit unless everything above is installed as intended: each v2
-- function has its v1 twin's attributes (not SECURITY DEFINER, the same
-- volatility, configuration and grants), carries the browser-zone predicate,
-- and each capture trigger calls its v2 function for every row event.
DO $check$
DECLARE pair text[];
BEGIN
    FOREACH pair SLICE 1 IN ARRAY ARRAY[
        ARRAY['stewardship_information_report_v1(uuid,jsonb,integer,uuid,integer)',
              'stewardship_information_report_v2(uuid,jsonb,integer,uuid,integer)',
              'AT TIME ZONE stewardship_timezone_name_v1(f->>''zone'')'],
        ARRAY['stewardship_ministry_report_v1(uuid,jsonb,boolean,bigint[],integer,text,integer,integer)',
              'stewardship_ministry_report_v2(uuid,jsonb,boolean,bigint[],integer,text,integer,integer)',
              'AT TIME ZONE stewardship_timezone_name_v1(filters->>''zone'')'],
        ARRAY['stewardship_information_export_capture_v1()',
              'stewardship_information_export_capture_v2()',
              'stewardship_information_report_v2(NEW.campaign_id,NEW.parameters)'],
        ARRAY['stewardship_ministry_export_capture_v1()',
              'stewardship_ministry_export_capture_v2()',
              'THEN stewardship_ministry_report_v2(NEW.campaign_id,filters,']]
    LOOP
        IF NOT EXISTS(
            SELECT 1 FROM pg_proc old, pg_proc new
            WHERE old.oid=('public.'||pair[1])::regprocedure
              AND new.oid=('public.'||pair[2])::regprocedure
              AND NOT new.prosecdef AND new.prosecdef=old.prosecdef
              AND new.provolatile=old.provolatile
              AND new.prolang=old.prolang
              AND new.prorettype=old.prorettype
              AND new.proconfig IS NOT DISTINCT FROM old.proconfig
              AND new.proacl IS NOT DISTINCT FROM old.proacl
              AND new.proowner=old.proowner
              AND position(pair[3] IN new.prosrc)>0)
        THEN RAISE EXCEPTION 'Migration 0017 did not install % as intended', pair[2];
        END IF;
    END LOOP;
    IF (SELECT count(*) FROM pg_trigger t
        WHERE NOT t.tgisinternal AND t.tgenabled='O' AND t.tgtype=31
          AND ((t.tgname='information_export_capture'
                AND t.tgrelid='public.stewardship_information_export_snapshot'::regclass
                AND t.tgfoid='public.stewardship_information_export_capture_v2()'::regprocedure)
            OR (t.tgname='ministry_export_capture'
                AND t.tgrelid='public.stewardship_ministry_export_snapshot'::regclass
                AND t.tgfoid='public.stewardship_ministry_export_capture_v2()'::regprocedure)))<>2
    THEN RAISE EXCEPTION 'Migration 0017 did not repoint the export capture triggers';
    END IF;
END
$check$;
