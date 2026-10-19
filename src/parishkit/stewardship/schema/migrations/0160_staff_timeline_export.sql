-- Frozen forward migration 0160: Staff may export a Family's Timeline (#1799).
-- It is installed by the Django migration that names it in FROZEN_SQL and
-- must never change once released; tests/stewardship/test_schema_migration_files.py
-- pins its digest and checks that its copies of the replaced baseline
-- functions equal the fresh-install baseline's (schema/exports.sql,
-- schema/ministry_exports.sql).
--
-- The Administrator decided on #1774 (2026-10-19) that Staff see a Family's
-- full Timeline; this opens its family_timeline export to them too. Three
-- functions are re-created from their latest bodies plus only these changes:
--   stewardship_timeline_export_capture_v1 (0109's): admits a capture for a
--     current Administrator or Staff member (stewardship_export_authorized_v1
--     without admin_only), keeps a Testing capture an Administrator's, and
--     raises the document bound from 128 KiB to 1 MiB, since the review lines
--     (#1774) lengthen a busy Family's timeline;
--   stewardship_export_request_guard_v1 (0109's): a timeline request is the
--     capture's own requester's or an Administrator's (regenerating another's
--     capture stays Administrator-only);
--   stewardship_export_request_authorized_v1 (0084's): the family_timeline
--     arm admits Administrators and Staff, as campaign reporting does.
-- Ministry leaders are still refused: stewardship_export_authorized_v1 admits
-- only the administrator and staff roles. None of the three is SECURITY
-- DEFINER, CREATE OR REPLACE keeps that and their owners and grants, and the
-- closing check confirms all of it. No table, row or grant changes.
-- Reversing needs its own forward migration.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

-- The three functions' grants as they are now, for the closing check.
SELECT set_config('parishkit.migration_0160_acl', coalesce((
    SELECT string_agg(proname||'='||coalesce(proacl::text,'-')||'/'||proowner::regrole::text,
        ';' ORDER BY proname)
    FROM pg_proc WHERE pronamespace='public'::regnamespace
      AND proname IN ('stewardship_timeline_export_capture_v1',
                      'stewardship_export_request_guard_v1',
                      'stewardship_export_request_authorized_v1')), ''), true);

-- stewardship_timeline_export_capture_v1: 0109_point_in_time_exports.sql's body plus #1799's changes.
CREATE OR REPLACE FUNCTION public.stewardship_timeline_export_capture_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE event jsonb;
BEGIN
    -- An ended export's discard (#1398, migration 0109), run as the schema
    -- owner, may clear the document; nothing else is ever changed.
    IF TG_OP='UPDATE' AND stewardship_export_capture_cleared_v1(
           TG_RELID,to_jsonb(OLD),to_jsonb(NEW)) THEN
        RETURN NEW;
    END IF;
    IF TG_OP<>'INSERT' THEN
        RAISE EXCEPTION 'Timeline export snapshots are immutable' USING ERRCODE='23514';
    END IF;
    PERFORM stewardship_export_campaign_lock_v1(NEW.campaign_id,false);
    IF current_user='pk_stewardship_worker'
       -- A current Administrator or Staff member (#1799): the page's full
       -- view is theirs since #1774.
       OR NOT stewardship_export_authorized_v1(NEW.actor_id)
       OR NOT stewardship_export_admitted_v1(NEW.campaign_id,true)
       OR NOT EXISTS(SELECT 1 FROM stewardship_system_configuration
           WHERE active_configuration_id=NEW.configuration_id)
       OR NOT EXISTS(SELECT 1 FROM stewardship_family_campaign f
           WHERE f.id=NEW.family_id AND f.campaign_id=NEW.campaign_id)
    THEN RAISE EXCEPTION 'Timeline export capture is unavailable' USING ERRCODE='23514'; END IF;
    IF jsonb_typeof(NEW.parameters) IS DISTINCT FROM 'object'
       OR (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(NEW.parameters) k)
           IS DISTINCT FROM ARRAY['family_id','mode','rehearsal_epoch_id']
       OR NEW.parameters->>'family_id' IS DISTINCT FROM NEW.family_id::text
       OR NOT ((NEW.parameters->>'mode'='production'
               AND NEW.parameters->'rehearsal_epoch_id'='null'::jsonb)
           OR (NEW.parameters->>'mode'='testing'
               -- Testing records stay an Administrator's alone (#1799).
               AND stewardship_export_authorized_v1(NEW.actor_id,true)
               AND jsonb_typeof(NEW.parameters->'rehearsal_epoch_id')='string'
               AND EXISTS(SELECT 1 FROM stewardship_rehearsal_epoch e
                   WHERE e.id::text=NEW.parameters->>'rehearsal_epoch_id'
                     AND e.campaign_id=NEW.campaign_id AND e.state='active')))
    THEN RAISE EXCEPTION 'Invalid timeline export parameters' USING ERRCODE='23514'; END IF;
    IF jsonb_typeof(NEW.document) IS DISTINCT FROM 'object'
       OR (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(NEW.document) k)
           IS DISTINCT FROM ARRAY['as_of','events','family','mode','summary']
       OR NEW.document->>'mode' IS DISTINCT FROM NEW.parameters->>'mode'
       OR jsonb_typeof(NEW.document->'as_of') IS DISTINCT FROM 'string'
       OR jsonb_typeof(NEW.document->'family') IS DISTINCT FROM 'object'
       OR jsonb_typeof(NEW.document->'events') IS DISTINCT FROM 'array'
       OR jsonb_typeof(NEW.document->'summary') IS DISTINCT FROM 'object'
       OR (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(NEW.document->'summary') k)
           IS DISTINCT FROM ARRAY['first_submitted_at','furthest_at','furthest_step',
               'last_email','last_seen_at','last_submitted_at','submissions']
       OR jsonb_typeof(NEW.document->'summary'->'submissions') IS DISTINCT FROM 'number'
       OR EXISTS(SELECT 1 FROM unnest(ARRAY['first_submitted_at','last_submitted_at',
               'furthest_step','furthest_at','last_seen_at']) field
           WHERE jsonb_typeof(NEW.document->'summary'->field) NOT IN ('string','null'))
       OR jsonb_typeof(NEW.document->'summary'->'last_email') NOT IN ('object','null')
       OR (jsonb_typeof(NEW.document->'summary'->'last_email')='object'
           AND (SELECT array_agg(k ORDER BY k)
               FROM jsonb_object_keys(NEW.document->'summary'->'last_email') k)
               IS DISTINCT FROM ARRAY['at','name','outcome'])
    THEN RAISE EXCEPTION 'Invalid timeline export document' USING ERRCODE='23514'; END IF;
    IF (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(NEW.document->'family') k)
           IS DISTINCT FROM ARRAY['duid','envelope','id','name','reach','reminder_workgroup']
       OR NEW.document->'family'->>'id' IS DISTINCT FROM NEW.family_id::text
       OR jsonb_typeof(NEW.document->'family'->'duid') IS DISTINCT FROM 'number'
       OR NOT EXISTS(SELECT 1 FROM stewardship_family_campaign f WHERE f.id=NEW.family_id
           AND f.family_duid::text=NEW.document->'family'->>'duid')
       OR jsonb_typeof(NEW.document->'family'->'name') NOT IN ('string','null')
       OR jsonb_typeof(NEW.document->'family'->'envelope') NOT IN ('number','null')
       OR jsonb_typeof(NEW.document->'family'->'reach') IS DISTINCT FROM 'string'
       OR jsonb_typeof(NEW.document->'family'->'reminder_workgroup') NOT IN ('string','null')
       -- One Family's timeline is small; a bound keeps a capture from
       -- growing without limit, as the Ministry capture's search bound does.
       -- 1 MiB since #1799 (128 KiB before): the review lines (#1774)
       -- lengthen a busy Family's timeline.
       OR octet_length(NEW.document::text)>1048576
    THEN RAISE EXCEPTION 'Invalid timeline export document' USING ERRCODE='23514'; END IF;
    FOR event IN SELECT value FROM jsonb_array_elements(NEW.document->'events') LOOP
        IF jsonb_typeof(event) IS DISTINCT FROM 'object' THEN
            RAISE EXCEPTION 'Invalid timeline export document' USING ERRCODE='23514';
        END IF;
        IF (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(event) k)
               IS DISTINCT FROM ARRAY['at','detail','what']
           OR jsonb_typeof(event->'at') IS DISTINCT FROM 'string'
           OR jsonb_typeof(event->'what') IS DISTINCT FROM 'string'
           OR jsonb_typeof(event->'detail') IS DISTINCT FROM 'string'
        THEN RAISE EXCEPTION 'Invalid timeline export document' USING ERRCODE='23514'; END IF;
    END LOOP;
    NEW.created_at:=statement_timestamp();
    NEW.row_count:=jsonb_array_length(NEW.document->'events');
    RETURN NEW;
END $$;

-- stewardship_export_request_guard_v1: the baseline's (0109's) body plus #1799's change.
CREATE OR REPLACE FUNCTION public.stewardship_export_request_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE facts stewardship_daily_fact_set%ROWTYPE;
        snapshot stewardship_information_export_snapshot%ROWTYPE;
        directory stewardship_directory_export_snapshot%ROWTYPE;
        ministry stewardship_ministry_export_snapshot%ROWTYPE;
        financial stewardship_financial_export_snapshot%ROWTYPE;
        names stewardship_family_test_names_export_snapshot%ROWTYPE;
        timeline stewardship_timeline_export_snapshot%ROWTYPE;
        comparison stewardship_comparison_export_snapshot%ROWTYPE;
        handoff boolean; inputs_valid boolean:=false;
BEGIN
    PERFORM stewardship_export_campaign_lock_v1(NEW.campaign_id,false);
    SELECT EXISTS(SELECT 1 FROM stewardship_exact_export_resolution x
        JOIN stewardship_exact_export_request r ON r.id=x.request_id
        WHERE x.export_id=NEW.id AND x.fact_set_id=NEW.fact_set_id
          AND ROW(NEW.campaign_id,NEW.requester_id,NEW.request_key,NEW.configuration_id,NEW.format,NEW.browser_timezone)=
              ROW(r.campaign_id,r.requester_id,NEW.id,r.configuration_id,r.format,r.browser_timezone)
          AND stewardship_fact_live(x.run_id,x.fence,x.worker_id)) INTO handoff;
    IF NEW.report='participation' THEN
        SELECT * INTO facts FROM stewardship_daily_fact_set WHERE id=NEW.fact_set_id FOR SHARE;
        inputs_valid:=facts.id IS NOT NULL AND facts.state='ready'
            AND facts.campaign_id=NEW.campaign_id
            AND NEW.parameters=jsonb_build_object('population_scope',facts.population_scope,
                'sort','date_asc','selected_ids','[]'::jsonb,'filters','{}'::jsonb);
    ELSIF NEW.report='ministry' THEN
        SELECT * INTO ministry FROM stewardship_ministry_export_snapshot WHERE id=NEW.ministry_snapshot_id;
        inputs_valid:=ministry.id IS NOT NULL AND ministry.campaign_id=NEW.campaign_id
            AND NEW.parameters=ministry.parameters AND NEW.authorization_scope=ministry.authorization_scope
            AND (ministry.actor_id=NEW.requester_id
                OR stewardship_export_authorized_v1(NEW.requester_id,true)
                OR EXISTS(SELECT 1 FROM stewardship_export_request prior
                    WHERE prior.ministry_snapshot_id=ministry.id AND prior.requester_id=NEW.requester_id));
    ELSIF NEW.report='additional_information' THEN
        SELECT * INTO snapshot FROM stewardship_information_export_snapshot WHERE id=NEW.information_snapshot_id;
        inputs_valid:=snapshot.id IS NOT NULL AND snapshot.campaign_id=NEW.campaign_id
            AND NEW.parameters=snapshot.parameters
            AND (snapshot.actor_id=NEW.requester_id
                OR stewardship_export_authorized_v1(NEW.requester_id,true)
                OR EXISTS(SELECT 1 FROM stewardship_export_request prior
                    WHERE prior.information_snapshot_id=snapshot.id
                      AND prior.requester_id=NEW.requester_id));
    ELSIF NEW.report IN ('family_directory','postal_outreach') THEN
        SELECT * INTO directory FROM stewardship_directory_export_snapshot WHERE id=NEW.directory_snapshot_id;
        inputs_valid:=directory.id IS NOT NULL AND directory.campaign_id=NEW.campaign_id
            AND NEW.parameters=directory.parameters
            AND (NEW.report='postal_outreach')=(directory.parameters->>'postal')::boolean
            AND (directory.actor_id=NEW.requester_id
                OR stewardship_export_authorized_v1(NEW.requester_id,true)
                OR EXISTS(SELECT 1 FROM stewardship_export_request prior
                    WHERE prior.directory_snapshot_id=directory.id
                      AND prior.requester_id=NEW.requester_id));
    ELSIF NEW.report='financial' THEN
        SELECT * INTO financial FROM stewardship_financial_export_snapshot WHERE id=NEW.financial_snapshot_id;
        inputs_valid:=financial.id IS NOT NULL AND financial.campaign_id=NEW.campaign_id
            AND NEW.parameters=financial.parameters
            AND (financial.actor_id=NEW.requester_id
                OR stewardship_export_authorized_v1(NEW.requester_id,true)
                OR EXISTS(SELECT 1 FROM stewardship_export_request prior
                    WHERE prior.financial_snapshot_id=financial.id
                      AND prior.requester_id=NEW.requester_id));
    ELSIF NEW.report='family_test_names' THEN
        SELECT * INTO names FROM stewardship_family_test_names_export_snapshot
            WHERE id=NEW.family_test_names_snapshot_id;
        inputs_valid:=names.id IS NOT NULL AND names.campaign_id=NEW.campaign_id
            AND NEW.parameters=names.parameters
            AND (names.actor_id=NEW.requester_id
                OR stewardship_export_authorized_v1(NEW.requester_id,true)
                OR EXISTS(SELECT 1 FROM stewardship_export_request prior
                    WHERE prior.family_test_names_snapshot_id=names.id
                      AND prior.requester_id=NEW.requester_id));
    ELSIF NEW.report='family_timeline' THEN
        -- One Family's timeline (migration 0063): its own capture, for the
        -- requester who captured it or an Administrator, who may also
        -- regenerate another's. Staff may request it since #1799; the
        -- request authorization checks the requester's role.
        SELECT * INTO timeline FROM stewardship_timeline_export_snapshot WHERE id=NEW.timeline_snapshot_id;
        inputs_valid:=timeline.id IS NOT NULL AND timeline.campaign_id=NEW.campaign_id
            AND NEW.parameters=timeline.parameters
            AND (timeline.actor_id=NEW.requester_id
                OR stewardship_export_authorized_v1(NEW.requester_id,true));
    ELSIF NEW.report='campaign_comparison' THEN
        -- The Campaign comparison (migration 0071): its own capture, for
        -- the requester who captured it, an Administrator, or a requester
        -- of an earlier export of it, as the financial capture.
        SELECT * INTO comparison FROM stewardship_comparison_export_snapshot
            WHERE id=NEW.comparison_snapshot_id;
        inputs_valid:=comparison.id IS NOT NULL AND comparison.campaign_id=NEW.campaign_id
            AND NEW.parameters=comparison.parameters
            AND (comparison.actor_id=NEW.requester_id
                OR stewardship_export_authorized_v1(NEW.requester_id,true)
                OR EXISTS(SELECT 1 FROM stewardship_export_request prior
                    WHERE prior.comparison_snapshot_id=comparison.id
                      AND prior.requester_id=NEW.requester_id));
    ELSIF NEW.report='campaign_archive' THEN
        -- The campaign archive workbook (migration 0084): an Administrator's
        -- XLSX of one ready historical fact set, cut off at a day that set
        -- holds, final or partial, named for its stewardship year. The
        -- worker reads the submissions itself under those pins.
        SELECT * INTO facts FROM stewardship_daily_fact_set WHERE id=NEW.fact_set_id FOR SHARE;
        inputs_valid:=facts.id IS NOT NULL AND facts.state='ready'
            AND facts.population_scope='historical' AND facts.campaign_id=NEW.campaign_id
            AND NEW.format='xlsx' AND jsonb_typeof(NEW.parameters)='object'
            AND (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(NEW.parameters) k)
                =ARRAY['cutoff_date','final','year']
            AND jsonb_typeof(NEW.parameters->'final')='boolean'
            AND jsonb_typeof(NEW.parameters->'year')='number'
            AND NEW.parameters->>'year' ~ '^2[01][0-9]{2}$'
            AND jsonb_typeof(NEW.parameters->'cutoff_date')='string'
            AND NEW.parameters->>'cutoff_date' ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
            AND EXISTS(SELECT 1 FROM stewardship_daily_fact d WHERE d.fact_set_id=facts.id
                AND d.local_date::text=NEW.parameters->>'cutoff_date')
            -- Only a closed or archived campaign's archive may be final.
            AND (NEW.parameters->'final'='false'::jsonb OR EXISTS(SELECT 1
                FROM stewardship_campaign c WHERE c.id=NEW.campaign_id
                  AND c.state IN ('closed','archived')))
            AND stewardship_export_authorized_v1(NEW.requester_id,true);
    END IF;
    IF inputs_valid IS DISTINCT FROM true OR NEW.actor_id IS DISTINCT FROM NEW.requester_id
       OR NOT stewardship_export_request_authorized_v1(NEW.requester_id,NEW)
       OR NOT stewardship_export_admitted_v1(NEW.campaign_id,true)
       OR (NOT handoff AND NOT EXISTS(SELECT 1 FROM stewardship_system_configuration
           WHERE active_configuration_id=NEW.configuration_id))
       OR (current_user='pk_stewardship_worker' AND NOT handoff)
       OR (NEW.report<>'ministry' AND NEW.authorization_scope<>'{"capability":"campaign_report"}'::jsonb)
       -- A capture an ended export's discard cleared is never used again
       -- (#1398, migration 0109): its file could not be made.
       OR '{}'::jsonb IN (snapshot.document,directory.document,ministry.document,
           financial.document,names.document,timeline.document,comparison.document)
       OR NOT EXISTS(SELECT 1 FROM pg_timezone_names
           WHERE name=public.stewardship_timezone_name_v1(NEW.browser_timezone))
       OR NOT EXISTS(SELECT 1 FROM stewardship_task_run t WHERE t.id=NEW.task_id
           AND t.root_id=t.id AND t.task_type='report_export' AND t.domain_request_id=NEW.id
           AND t.initiated_by_id=NEW.requester_id AND t.idempotency_key=NEW.id::text
           AND t.state='queued')
    THEN RAISE EXCEPTION 'Export request lacks exact authorized input/task binding'
        USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;

-- stewardship_export_request_authorized_v1: the baseline's (0084's) body plus #1799's change.
CREATE OR REPLACE FUNCTION public.stewardship_export_request_authorized_v1(user_uuid uuid, request stewardship_export_request)
RETURNS boolean LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT (user_uuid=request.requester_id OR stewardship_export_authorized_v1(user_uuid,true))
        AND CASE WHEN request.report='ministry' THEN
            stewardship_ministry_scope_authorized_v1(user_uuid,request.authorization_scope)
        WHEN request.report='family_timeline' THEN
            -- One Family's full timeline is the page's view of Administrators
            -- and Staff (#1799), for the requester as for anyone else.
            stewardship_export_authorized_v1(user_uuid)
        WHEN request.report='campaign_archive' THEN
            -- The campaign archive workbook holds each Family's giving
            -- (migration 0084): Administrators only, as Past campaigns.
            stewardship_export_authorized_v1(user_uuid,true)
        ELSE stewardship_export_authorized_v1(user_uuid) END
$$;

-- Refuse to commit unless all three new bodies are installed (each still
-- holding the earlier rules it must keep), none SECURITY DEFINER, the
-- capture trigger still runs the capture function, and the owners and
-- grants are unchanged. No temporary objects: the migration login has no
-- TEMP grant.
DO $check$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM pg_proc
        WHERE pronamespace='public'::regnamespace
          AND proname='stewardship_timeline_export_capture_v1' AND NOT prosecdef
          AND position('OR NOT stewardship_export_authorized_v1(NEW.actor_id)' IN prosrc)>0
          AND position('OR NOT stewardship_export_authorized_v1(NEW.actor_id,true)' IN prosrc)=0
          AND position('AND stewardship_export_authorized_v1(NEW.actor_id,true)' IN prosrc)>0
          AND position('octet_length(NEW.document::text)>1048576' IN prosrc)>0
          AND position('131072' IN prosrc)=0
          AND position('stewardship_export_capture_cleared_v1' IN prosrc)>0)
    THEN
        RAISE EXCEPTION 'Migration 0160: the timeline capture does not admit Staff';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM pg_proc
        WHERE pronamespace='public'::regnamespace
          AND proname='stewardship_export_request_guard_v1' AND NOT prosecdef
          AND position('AND (timeline.actor_id=NEW.requester_id' IN prosrc)>0
          AND position('snapshot.document,directory.document' IN prosrc)>0
          AND position('stewardship_comparison_export_snapshot' IN prosrc)>0
          AND position('campaign_archive' IN prosrc)>0)
       OR NOT EXISTS(SELECT 1 FROM pg_proc
        WHERE pronamespace='public'::regnamespace
          AND proname='stewardship_export_request_authorized_v1' AND NOT prosecdef
          AND position('and Staff (#1799)' IN prosrc)>0
          -- The arm's code itself: Administrators and Staff, not admin_only.
          AND position(E'stewardship_export_authorized_v1(user_uuid)\n        WHEN request.report=''campaign_archive''' IN prosrc)>0
          AND position('stewardship_ministry_scope_authorized_v1' IN prosrc)>0
          AND position('campaign_archive' IN prosrc)>0)
    THEN
        RAISE EXCEPTION 'Migration 0160: the timeline export request does not admit Staff';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM pg_trigger t JOIN pg_proc p ON p.oid=t.tgfoid
        WHERE t.tgrelid='public.stewardship_timeline_export_snapshot'::regclass
          AND t.tgname='timeline_export_capture' AND t.tgenabled='O'
          AND p.proname='stewardship_timeline_export_capture_v1')
       OR current_setting('parishkit.migration_0160_acl') IS DISTINCT FROM coalesce((
        SELECT string_agg(proname||'='||coalesce(proacl::text,'-')||'/'||proowner::regrole::text,
            ';' ORDER BY proname)
        FROM pg_proc WHERE pronamespace='public'::regnamespace
          AND proname IN ('stewardship_timeline_export_capture_v1',
                          'stewardship_export_request_guard_v1',
                          'stewardship_export_request_authorized_v1')), '')
       OR (SELECT count(*) FROM pg_proc WHERE pronamespace='public'::regnamespace
          AND proname IN ('stewardship_timeline_export_capture_v1',
                          'stewardship_export_request_guard_v1',
                          'stewardship_export_request_authorized_v1'))<>3
    THEN
        RAISE EXCEPTION 'Migration 0160: the timeline export guards changed owner, grants or trigger';
    END IF;
END
$check$;
