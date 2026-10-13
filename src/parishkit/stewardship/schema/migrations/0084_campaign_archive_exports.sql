-- Frozen forward migration file 0084 (the repository-wide file sequence):
-- the campaign archive download (#1018 part (a), slice A2). This file is
-- installed by its Django migration in the reports app and must never
-- change once released; tests/stewardship/test_schema_migration_files.py
-- pins its digest and checks that its copies of the replaced export
-- functions still equal the fresh-install baseline's (schema/exports.sql and
-- schema/ministry_exports.sql). A later change gets its own numbered file.
--
-- The Administrator's Download campaign archive (Past campaigns, and
-- pk-admin export archive) queues the shared export lifecycle's
-- campaign_archive kind: one XLSX workbook in the campaign archive format,
-- written by the general worker from one ready historical fact set (its
-- submission watermark) and a cutoff day, the request's two pins. No capture
-- table: the worker reads the submissions itself under those pins. It:
--   - creates stewardship_campaign_archive_answers_v1, a definer function
--     that gives the general worker only the archive's parts of each live
--     answer under a fact set's pins (frequency, the cannot-give box, the
--     Ministry choices and the chosen Ministries' names), never the rest of
--     an answer, so the worker needs no grant on the answers column;
--   - re-adds export_report_known with 0071's arms plus campaign_archive
--     (XLSX, a fact set and no capture), in the form PostgreSQL deparses
--     Django's declaration;
--   - replaces stewardship_export_request_guard_v1 and
--     stewardship_export_publication_guard_v1 with their latest bodies
--     (0071's) plus one branch each, stewardship_export_pin_guard_v1 so the
--     new kind needs its fact pin as participation does, and
--     stewardship_export_request_authorized_v1 (0063's) so only an
--     Administrator may request, render, cancel or download one.
-- None of the four replaced functions is SECURITY DEFINER in the baseline,
-- and CREATE OR REPLACE keeps that; the new function's SECURITY DEFINER is
-- stated again after it, as for every definer function here. The check at
-- the end confirms all of it. Grants follow from runtime_grants.py through
-- the upgrade's database-grants step. No existing row changes. Reversing
-- needs its own forward migration.
--
-- Re-chain note: numbers 0077 to 0079 are reserved by open pull requests
-- (#1174, #1177, #1180); the Django migration installing this file depends
-- on the one installing 0076 until they merge, and is re-chained then.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

-- The archive's parts of each live answer under one ready historical fact
-- set's pins: the versions of its campaign up to its submission watermark
-- and submitted before the cutoff instant. Only the general worker (and the
-- schema owner) may call it. It returns the frequency word, the cannot-give
-- box (both null without a financial answer), the Ministry choices and the
-- names of the chosen Ministries in the version's reviewed source snapshot.
CREATE FUNCTION public.stewardship_campaign_archive_answers_v1(
    fact_set uuid, cutoff timestamp with time zone)
RETURNS TABLE(version bigint, frequency text, cannot_give boolean,
    ministries jsonb, ministry_names jsonb)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE selected record;
BEGIN
    IF session_user<>'pk_stewardship_worker' AND NOT pg_has_role(session_user,
        (SELECT nspowner FROM pg_namespace WHERE nspname='public'),'USAGE') THEN
        RAISE EXCEPTION 'Only the general worker reads campaign archive answers' USING ERRCODE='42501';
    END IF;
    IF fact_set IS NULL OR cutoff IS NULL THEN
        RAISE EXCEPTION 'Campaign archive answers need a fact set and a cutoff' USING ERRCODE='22023';
    END IF;
    SELECT s.campaign_id, s.submission_watermark INTO selected
    FROM public.stewardship_daily_fact_set s
    WHERE s.id=fact_set AND s.state='ready' AND s.population_scope='historical';
    IF NOT FOUND THEN
        RETURN;
    END IF;
    RETURN QUERY
    WITH answers AS (
        SELECT r.campaign_sequence, r.reviewed_source_id,
            CASE WHEN jsonb_typeof(r.answers->'financial')='object'
                THEN r.answers->'financial' END AS financial,
            CASE WHEN jsonb_typeof(r.answers->'ministries')='object'
                THEN r.answers->'ministries' ELSE '{}'::jsonb END AS choices
        FROM public.stewardship_submission r
        WHERE r.campaign_id=selected.campaign_id AND r.mode='live'
          AND r.campaign_sequence<=selected.submission_watermark AND r.submitted_at<cutoff
    )
    SELECT a.campaign_sequence::bigint,
        nullif(a.financial->>'frequency',''),
        CASE WHEN a.financial IS NOT NULL
            THEN coalesce(a.financial->'cannot_give'='true'::jsonb,false) END,
        a.choices,
        coalesce((
            SELECT jsonb_object_agg(m.source_key, coalesce((v.canonical::jsonb)->>'name',''))
            FROM public.stewardship_snapshot_ministry m
            JOIN public.stewardship_source_ministry v ON v.id=m.payload_id
            WHERE m.snapshot_id=a.reviewed_source_id
              AND m.source_key IN (
                  SELECT jsonb_array_elements_text(choice.value)
                  FROM jsonb_each(a.choices) grp
                  CROSS JOIN LATERAL jsonb_each(CASE WHEN jsonb_typeof(grp.value)='object'
                      THEN grp.value ELSE '{}'::jsonb END) person
                  CROSS JOIN LATERAL jsonb_each(CASE WHEN jsonb_typeof(person.value)='object'
                      THEN person.value ELSE '{}'::jsonb END) choice
                  WHERE jsonb_typeof(choice.value)='array')), '{}'::jsonb)
    FROM answers a
    ORDER BY a.campaign_sequence;
END $$;
ALTER FUNCTION public.stewardship_campaign_archive_answers_v1(uuid, timestamp with time zone) SECURITY DEFINER;
REVOKE ALL ON FUNCTION public.stewardship_campaign_archive_answers_v1(uuid, timestamp with time zone) FROM PUBLIC;

-- The known report kinds, in the form PostgreSQL deparses the model's
-- declaration: 0071's arms plus campaign_archive, which needs a fact set,
-- no capture and the XLSX format. ADD CONSTRAINT validates the existing
-- rows, which are all of the earlier kinds.
ALTER TABLE public.stewardship_export_request DROP CONSTRAINT export_report_known;

ALTER TABLE public.stewardship_export_request ADD CONSTRAINT export_report_known CHECK ((((comparison_snapshot_id IS NULL) AND (directory_snapshot_id IS NULL) AND (fact_set_id IS NOT NULL) AND (family_test_names_snapshot_id IS NULL) AND (financial_snapshot_id IS NULL) AND (information_snapshot_id IS NULL) AND (ministry_snapshot_id IS NULL) AND ((report)::text = 'participation'::text) AND (timeline_snapshot_id IS NULL)) OR ((comparison_snapshot_id IS NULL) AND (directory_snapshot_id IS NULL) AND (fact_set_id IS NULL) AND (family_test_names_snapshot_id IS NULL) AND (financial_snapshot_id IS NULL) AND ((format)::text = ANY ((ARRAY['csv'::character varying, 'xlsx'::character varying, 'pdf'::character varying])::text[])) AND (information_snapshot_id IS NOT NULL) AND (ministry_snapshot_id IS NULL) AND ((report)::text = 'additional_information'::text) AND (timeline_snapshot_id IS NULL)) OR ((comparison_snapshot_id IS NULL) AND (directory_snapshot_id IS NOT NULL) AND (fact_set_id IS NULL) AND (family_test_names_snapshot_id IS NULL) AND (financial_snapshot_id IS NULL) AND ((format)::text = ANY ((ARRAY['csv'::character varying, 'xlsx'::character varying, 'pdf'::character varying])::text[])) AND (information_snapshot_id IS NULL) AND (ministry_snapshot_id IS NULL) AND ((report)::text = ANY ((ARRAY['family_directory'::character varying, 'postal_outreach'::character varying])::text[])) AND (timeline_snapshot_id IS NULL)) OR ((comparison_snapshot_id IS NULL) AND (directory_snapshot_id IS NULL) AND (fact_set_id IS NULL) AND (family_test_names_snapshot_id IS NULL) AND (financial_snapshot_id IS NULL) AND ((format)::text = ANY ((ARRAY['csv'::character varying, 'xlsx'::character varying, 'pdf'::character varying])::text[])) AND (information_snapshot_id IS NULL) AND (ministry_snapshot_id IS NOT NULL) AND ((report)::text = 'ministry'::text) AND (timeline_snapshot_id IS NULL)) OR ((comparison_snapshot_id IS NULL) AND (directory_snapshot_id IS NULL) AND (fact_set_id IS NULL) AND (family_test_names_snapshot_id IS NULL) AND (financial_snapshot_id IS NOT NULL) AND ((format)::text = ANY ((ARRAY['csv'::character varying, 'xlsx'::character varying, 'pdf'::character varying])::text[])) AND (information_snapshot_id IS NULL) AND (ministry_snapshot_id IS NULL) AND ((report)::text = 'financial'::text) AND (timeline_snapshot_id IS NULL)) OR ((comparison_snapshot_id IS NULL) AND (directory_snapshot_id IS NULL) AND (fact_set_id IS NULL) AND (family_test_names_snapshot_id IS NOT NULL) AND (financial_snapshot_id IS NULL) AND ((format)::text = ANY ((ARRAY['csv'::character varying, 'xlsx'::character varying, 'pdf'::character varying])::text[])) AND (information_snapshot_id IS NULL) AND (ministry_snapshot_id IS NULL) AND ((report)::text = 'family_test_names'::text) AND (timeline_snapshot_id IS NULL)) OR ((comparison_snapshot_id IS NULL) AND (directory_snapshot_id IS NULL) AND (fact_set_id IS NULL) AND (family_test_names_snapshot_id IS NULL) AND (financial_snapshot_id IS NULL) AND ((format)::text = ANY ((ARRAY['csv'::character varying, 'xlsx'::character varying, 'pdf'::character varying])::text[])) AND (information_snapshot_id IS NULL) AND (ministry_snapshot_id IS NULL) AND ((report)::text = 'family_timeline'::text) AND (timeline_snapshot_id IS NOT NULL)) OR ((comparison_snapshot_id IS NOT NULL) AND (directory_snapshot_id IS NULL) AND (fact_set_id IS NULL) AND (family_test_names_snapshot_id IS NULL) AND (financial_snapshot_id IS NULL) AND ((format)::text = ANY ((ARRAY['csv'::character varying, 'xlsx'::character varying, 'pdf'::character varying])::text[])) AND (information_snapshot_id IS NULL) AND (ministry_snapshot_id IS NULL) AND ((report)::text = 'campaign_comparison'::text) AND (timeline_snapshot_id IS NULL)) OR ((comparison_snapshot_id IS NULL) AND (directory_snapshot_id IS NULL) AND (fact_set_id IS NOT NULL) AND (family_test_names_snapshot_id IS NULL) AND (financial_snapshot_id IS NULL) AND ((format)::text = 'xlsx'::text) AND (information_snapshot_id IS NULL) AND (ministry_snapshot_id IS NULL) AND ((report)::text = 'campaign_archive'::text) AND (timeline_snapshot_id IS NULL))));

-- The export guards and the request authorization: their latest bodies
-- plus the archive branch (see the header).
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
        -- One Family's timeline (migration 0063): Administrator only, as
        -- the page's full view, so any current Administrator may also
        -- regenerate another's capture.
        SELECT * INTO timeline FROM stewardship_timeline_export_snapshot WHERE id=NEW.timeline_snapshot_id;
        inputs_valid:=timeline.id IS NOT NULL AND timeline.campaign_id=NEW.campaign_id
            AND NEW.parameters=timeline.parameters
            AND stewardship_export_authorized_v1(NEW.requester_id,true);
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

CREATE OR REPLACE FUNCTION public.stewardship_export_publication_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE request stewardship_export_request%ROWTYPE;
BEGIN
    PERFORM pg_advisory_xact_lock(736220,1);
    SELECT * INTO request FROM stewardship_export_request WHERE id=NEW.request_id;
    IF request.id IS NULL OR NOT stewardship_export_admitted_v1(request.campaign_id,true)
       OR NOT stewardship_export_request_authorized_v1(request.requester_id,request)
       OR EXISTS(SELECT 1 FROM stewardship_export_cancellation WHERE request_id=request.id)
       OR NEW.expires_at > NEW.created_at + interval '7 days 1 minute'
       OR NOT (
           (request.report='participation' AND EXISTS(SELECT 1 FROM stewardship_daily_fact_set f
               WHERE f.id=request.fact_set_id AND f.state='ready' AND f.expected_count=NEW.row_count))
           OR (request.report='additional_information' AND EXISTS(
               SELECT 1 FROM stewardship_information_export_snapshot s
               WHERE s.id=request.information_snapshot_id AND s.row_count=NEW.row_count))
           OR (request.report IN ('family_directory','postal_outreach') AND EXISTS(
               SELECT 1 FROM stewardship_directory_export_snapshot s
               WHERE s.id=request.directory_snapshot_id AND s.row_count=NEW.row_count))
           OR (request.report='ministry' AND EXISTS(
               SELECT 1 FROM stewardship_ministry_export_snapshot s
               WHERE s.id=request.ministry_snapshot_id AND s.row_count=NEW.row_count))
           OR (request.report='financial' AND EXISTS(
               SELECT 1 FROM stewardship_financial_export_snapshot s
               WHERE s.id=request.financial_snapshot_id AND s.row_count=NEW.row_count))
           OR (request.report='family_test_names' AND EXISTS(
               SELECT 1 FROM stewardship_family_test_names_export_snapshot s
               WHERE s.id=request.family_test_names_snapshot_id AND s.row_count=NEW.row_count))
           OR (request.report='family_timeline' AND EXISTS(
               SELECT 1 FROM stewardship_timeline_export_snapshot s
               WHERE s.id=request.timeline_snapshot_id AND s.row_count=NEW.row_count))
           OR (request.report='campaign_comparison' AND EXISTS(
               SELECT 1 FROM stewardship_comparison_export_snapshot s
               WHERE s.id=request.comparison_snapshot_id AND s.row_count=NEW.row_count))
           OR (request.report='campaign_archive' AND NEW.row_count>0 AND EXISTS(
               SELECT 1 FROM stewardship_daily_fact_set f
               WHERE f.id=request.fact_set_id AND f.state='ready')))
       OR NOT EXISTS(SELECT 1 FROM stewardship_export_attempt a JOIN stewardship_task_run t ON t.id=a.run_id
           WHERE a.id=NEW.attempt_id AND a.request_id=request.id AND a.actor_id=NEW.actor_id
             AND t.state='running' AND t.fence=a.fence AND t.worker_id=a.actor_id
             AND t.lease_expires_at>clock_timestamp())
    THEN RAISE EXCEPTION 'Export publication lacks live authorized ownership' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION public.stewardship_export_pin_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF NEW.report IN ('participation','campaign_archive') AND NOT EXISTS(SELECT 1 FROM stewardship_fact_pin WHERE fact_set_id=NEW.fact_set_id
        AND parent_kind='export' AND parent_id=NEW.id)
    THEN RAISE EXCEPTION 'Export requires its retained fact pin' USING ERRCODE='23514'; END IF;
    RETURN NULL;
END $$;

CREATE OR REPLACE FUNCTION public.stewardship_export_request_authorized_v1(user_uuid uuid, request stewardship_export_request)
RETURNS boolean LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT (user_uuid=request.requester_id OR stewardship_export_authorized_v1(user_uuid,true))
        AND CASE WHEN request.report='ministry' THEN
            stewardship_ministry_scope_authorized_v1(user_uuid,request.authorization_scope)
        WHEN request.report='family_timeline' THEN
            -- One Family's full timeline is the page's Administrator view
            -- (migration 0063), for the requester as for anyone else.
            stewardship_export_authorized_v1(user_uuid,true)
        WHEN request.report='campaign_archive' THEN
            -- The campaign archive workbook holds each Family's giving
            -- (migration 0084): Administrators only, as Past campaigns.
            stewardship_export_authorized_v1(user_uuid,true)
        ELSE stewardship_export_authorized_v1(user_uuid) END
$$;

-- Refuse to commit unless every part above is installed: the definer
-- function as declared, the widened kinds (checked against synthetic rows,
-- as 0063's check does), and the four replaced functions with their archive
-- branch and their earlier branches, none of them SECURITY DEFINER. No
-- temporary objects: the migration login has no TEMP grant.
DO $check$
DECLARE expression text; fmt text; admitted boolean; probe json;
BEGIN
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname='stewardship_campaign_archive_answers_v1'
          AND p.prosecdef AND p.provolatile='s' AND p.proretset
          AND p.proconfig @> ARRAY['search_path=pg_catalog, public, pg_temp']
          AND pg_get_function_identity_arguments(p.oid)='fact_set uuid, cutoff timestamp with time zone'
          AND p.prosrc LIKE '%session_user<>''pk_stewardship_worker''%'
          AND p.prosrc LIKE '%r.campaign_sequence<=selected.submission_watermark AND r.submitted_at<cutoff%'
          AND p.prosrc LIKE '%s.population_scope=''historical''%'
          AND NOT has_function_privilege('public', p.oid, 'EXECUTE'))<>1
       OR (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
           WHERE n.nspname='public' AND p.proname='stewardship_campaign_archive_answers_v1')<>1
    THEN
        RAISE EXCEPTION 'Migration 0084: the campaign archive answers function is not installed as declared';
    END IF;
    SELECT pg_get_expr(conbin, conrelid) INTO expression FROM pg_constraint
     WHERE conrelid='public.stewardship_export_request'::regclass
       AND conname='export_report_known' AND contype='c' AND convalidated;
    IF expression IS NULL THEN
        RAISE EXCEPTION 'Migration 0084: export_report_known does not admit the archive kind';
    END IF;
    -- An archive request is admitted in XLSX only, with its fact set and
    -- no capture; the comparison and timeline kinds still hold.
    FOREACH fmt IN ARRAY ARRAY['csv','xlsx','pdf','png'] LOOP
        FOR probe IN SELECT unnest(ARRAY[
            json_build_object('report','campaign_archive','format',fmt,
                'fact_set_id','00000000-0000-4000-8000-000000000000'),
            json_build_object('report','campaign_comparison','format',fmt,
                'comparison_snapshot_id','00000000-0000-4000-8000-000000000000'),
            json_build_object('report','family_timeline','format',fmt,
                'timeline_snapshot_id','00000000-0000-4000-8000-000000000000')])
        LOOP
            EXECUTE format(
                'SELECT %s FROM json_populate_record(NULL::public.stewardship_export_request, $1) AS stewardship_export_request',
                expression) INTO admitted USING probe;
            IF admitted IS DISTINCT FROM (CASE WHEN probe->>'report'='campaign_archive'
                THEN fmt='xlsx' ELSE fmt<>'png' END) THEN
                RAISE EXCEPTION 'Migration 0084: export_report_known does not admit the archive kind';
            END IF;
        END LOOP;
    END LOOP;
    FOR probe IN SELECT unnest(ARRAY[
        json_build_object('report','campaign_archive','format','xlsx'),
        json_build_object('report','campaign_archive','format','xlsx',
            'fact_set_id','00000000-0000-4000-8000-000000000000',
            'comparison_snapshot_id','00000000-0000-4000-8000-000000000000')])
    LOOP
        EXECUTE format(
            'SELECT %s FROM json_populate_record(NULL::public.stewardship_export_request, $1) AS stewardship_export_request',
            expression) INTO admitted USING probe;
        IF admitted IS DISTINCT FROM false THEN
            RAISE EXCEPTION 'Migration 0084: export_report_known does not admit the archive kind';
        END IF;
    END LOOP;
    IF (SELECT count(*) FROM pg_proc
        WHERE pronamespace='public'::regnamespace
          AND proname IN ('stewardship_export_request_guard_v1',
                          'stewardship_export_publication_guard_v1')
          AND position('campaign_archive' IN prosrc)>0
          AND position('stewardship_comparison_export_snapshot' IN prosrc)>0
          AND position('stewardship_timeline_export_snapshot' IN prosrc)>0
          AND NOT prosecdef)<>2
       OR NOT EXISTS(SELECT 1 FROM pg_proc
           WHERE pronamespace='public'::regnamespace
             AND proname='stewardship_export_pin_guard_v1'
             AND position('IN (''participation'',''campaign_archive'')' IN prosrc)>0
             AND NOT prosecdef)
       OR NOT EXISTS(SELECT 1 FROM pg_proc
           WHERE pronamespace='public'::regnamespace
             AND proname='stewardship_export_request_authorized_v1'
             AND position('campaign_archive' IN prosrc)>0
             AND position('family_timeline' IN prosrc)>0
             AND position('stewardship_ministry_scope_authorized_v1' IN prosrc)>0
             AND NOT prosecdef)
    THEN
        RAISE EXCEPTION 'Migration 0084: the export guards were not replaced';
    END IF;
END
$check$;
