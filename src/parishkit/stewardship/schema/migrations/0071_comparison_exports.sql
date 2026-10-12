-- Frozen forward migration file 0071 (the repository-wide file sequence):
-- the Campaign comparison downloads (#1018, slice 6). This file is installed
-- by its Django migration in the reports app and must never change once
-- released; tests/stewardship/test_schema_migration_files.py pins its digest
-- and checks that its copies of the two replaced export guards still equal
-- the fresh-install baseline's (schema/exports.sql). A later change gets its
-- own numbered file. A fresh install runs the baseline, the earlier files
-- and then this; the baseline already carries the two guards' new bodies,
-- and this file adds the table, the column, the constraint and the guards'
-- copies, so the install ends in the same catalog as an upgraded database.
--
-- The Campaign comparison page's CSV, XLSX and PDF summary leave the Admin
-- portal, so the web stores what they may hold, the comparison with the
-- campaign-history spec's small-count protection already applied, in an
-- immutable stewardship_comparison_export_snapshot, and the shared export
-- lifecycle renders, publishes and downloads it as report kind
-- campaign_comparison. The page reads its figures in Python, so the web
-- supplies the captured document; this file checks everything around it.
-- It:
--   - creates the capture table, as Django renders ComparisonExportSnapshot,
--     with its capture guard (insert only, from the web, by an
--     Administrator or Staff member, for an admitted campaign under the
--     active configuration, and the document's closed shape) and its
--     deferred binding to an export request;
--   - adds stewardship_export_request.comparison_snapshot_id with its
--     deferrable foreign key and index;
--   - replaces export_report_known with 0063's list (the latest; 0049's
--     family_test_names arm in CSV, XLSX or PDF and 0063's family_timeline
--     arm) plus the campaign_comparison arm (CSV, XLSX or PDF), every
--     earlier arm also requiring the new column to be NULL, in the form
--     PostgreSQL deparses Django's declaration;
--   - replaces stewardship_export_request_guard_v1 and
--     stewardship_export_publication_guard_v1 with their latest bodies
--     (0063's, with its family_timeline branches, which exports.sql
--     carries) plus one branch each for the new kind.
--     stewardship_export_request_authorized_v1 (last replaced by 0063)
--     needs no change: its ELSE branch (Administrators and Staff) is the
--     page's own access.
-- Neither replaced guard is SECURITY DEFINER in the baseline, and CREATE OR
-- REPLACE keeps that; the check at the end confirms it. Grants follow from
-- reports/export_grants.py through the upgrade's database-grants step. No
-- existing row changes. Reversing needs its own forward migration.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

-- The capture table, as Django renders the model.
CREATE TABLE "stewardship_comparison_export_snapshot" ("id" uuid NOT NULL PRIMARY KEY, "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "campaign_id" uuid NOT NULL, "configuration_id" uuid NOT NULL, "actor_id" uuid NOT NULL, "correlation_id" uuid NOT NULL, "parameters" jsonb NOT NULL, "document" jsonb NOT NULL, "row_count" integer NOT NULL CHECK ("row_count" >= 0));
ALTER TABLE "stewardship_comparison_export_snapshot" ADD CONSTRAINT "stewardship_comparis_campaign_id_0ed9beb2_fk_stewardsh" FOREIGN KEY ("campaign_id") REFERENCES "stewardship_campaign" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_comparison_export_snapshot" ADD CONSTRAINT "stewardship_comparis_configuration_id_162de766_fk_stewardsh" FOREIGN KEY ("configuration_id") REFERENCES "stewardship_configuration_version" ("id") DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX "comparison_export_correlation" ON "stewardship_comparison_export_snapshot" ("correlation_id");
CREATE INDEX "comparison_export_campaign" ON "stewardship_comparison_export_snapshot" ("campaign_id");
CREATE INDEX "comparison_export_config" ON "stewardship_comparison_export_snapshot" ("configuration_id");

-- The web's capture is admitted only for a current Administrator or Staff
-- member (the page's own access), from the web login, for a campaign that
-- admits exports, under the active configuration. Its parameters are
-- exactly the comparison day (a whole number from 1, or null before day 1
-- has ended), which the document repeats. The document's shape is closed
-- (these keys and no other), as defense in depth: a caller cannot add a
-- field the files were not designed to hold. It cannot tell a note's text
-- from a figure's; keeping notes and Family values out is the job of the
-- web's capture (comparison_exports.capture). Every Every number row has
-- one cell per column, each a number, text, blank, an exact amount
-- ({"money": "1234.50"}) or a date
-- ({"date": "2026-09-13"}); each tile is a label, a value and its lines;
-- each chart a title, axis, kind, last day and lines of [day, value]
-- points. The trigger sets the time and the row count. Rows never change.
CREATE FUNCTION public.stewardship_comparison_export_capture_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE width integer; item jsonb; value jsonb;
BEGIN
    IF TG_OP<>'INSERT' THEN
        RAISE EXCEPTION 'Comparison export captures are immutable' USING ERRCODE='23514';
    END IF;
    PERFORM stewardship_export_campaign_lock_v1(NEW.campaign_id,false);
    IF current_user='pk_stewardship_worker'
       OR NOT stewardship_export_authorized_v1(NEW.actor_id)
       OR NOT stewardship_export_admitted_v1(NEW.campaign_id,true)
       OR NOT EXISTS(SELECT 1 FROM stewardship_system_configuration
           WHERE active_configuration_id=NEW.configuration_id)
    THEN RAISE EXCEPTION 'Comparison export capture is unavailable' USING ERRCODE='23514'; END IF;
    IF jsonb_typeof(NEW.parameters) IS DISTINCT FROM 'object'
       OR (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(NEW.parameters) k)
           IS DISTINCT FROM ARRAY['day']
       OR NOT (NEW.parameters->'day'='null'::jsonb
           OR (jsonb_typeof(NEW.parameters->'day')='number'
               AND NEW.parameters->>'day' ~ '^[1-9][0-9]{0,3}$'))
    THEN RAISE EXCEPTION 'Invalid comparison export parameters' USING ERRCODE='23514'; END IF;
    IF jsonb_typeof(NEW.document) IS DISTINCT FROM 'object'
       OR (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(NEW.document) k)
           IS DISTINCT FROM ARRAY['campaign','charts','columns','compared_with','day',
               'financial','report_date','rows','tiles','updating']
       OR NEW.document->'day' IS DISTINCT FROM NEW.parameters->'day'
       OR jsonb_typeof(NEW.document->'campaign') IS DISTINCT FROM 'string'
       OR jsonb_typeof(NEW.document->'compared_with') IS DISTINCT FROM 'string'
       OR jsonb_typeof(NEW.document->'financial') IS DISTINCT FROM 'boolean'
       OR jsonb_typeof(NEW.document->'updating') IS DISTINCT FROM 'boolean'
       OR jsonb_typeof(NEW.document->'report_date') IS DISTINCT FROM
           (CASE WHEN NEW.parameters->'day'='null'::jsonb THEN 'null' ELSE 'string' END)
       OR (NEW.document->'report_date'<>'null'::jsonb
           AND NEW.document->>'report_date' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$')
       OR EXISTS(SELECT 1 FROM unnest(ARRAY['tiles','columns','rows','charts']) part
           WHERE jsonb_typeof(NEW.document->part) IS DISTINCT FROM 'array')
       OR jsonb_array_length(NEW.document->'columns')=0
       OR EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.document->'columns') c
           WHERE jsonb_typeof(c)<>'string')
       -- A comparison is a few rows and charts of a few campaigns; a bound
       -- keeps a capture from growing without limit.
       OR octet_length(NEW.document::text)>524288
    THEN RAISE EXCEPTION 'Invalid comparison export document' USING ERRCODE='23514'; END IF;
    width:=jsonb_array_length(NEW.document->'columns');
    FOR item IN SELECT r FROM jsonb_array_elements(NEW.document->'rows') r LOOP
        IF jsonb_typeof(item) IS DISTINCT FROM 'array' OR jsonb_array_length(item)<>width THEN
            RAISE EXCEPTION 'Invalid comparison export document' USING ERRCODE='23514';
        END IF;
        FOR value IN SELECT v FROM jsonb_array_elements(item) v LOOP
            IF NOT (jsonb_typeof(value) IN ('null','number','string')
                OR (jsonb_typeof(value)='object'
                    AND (SELECT array_agg(k) FROM jsonb_object_keys(value) k)=ARRAY['money']
                    AND jsonb_typeof(value->'money')='string'
                    AND value->>'money' ~ '^-?[0-9]{1,20}\.[0-9]{2}$')
                OR (jsonb_typeof(value)='object'
                    AND (SELECT array_agg(k) FROM jsonb_object_keys(value) k)=ARRAY['date']
                    AND jsonb_typeof(value->'date')='string'
                    AND value->>'date' ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'))
            THEN RAISE EXCEPTION 'Invalid comparison export document' USING ERRCODE='23514'; END IF;
        END LOOP;
    END LOOP;
    IF EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.document->'tiles') t
        WHERE jsonb_typeof(t) IS DISTINCT FROM 'object'
           OR (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(t) k)
               IS DISTINCT FROM ARRAY['label','lines','value']
           OR jsonb_typeof(t->'label')<>'string' OR jsonb_typeof(t->'value')<>'string'
           OR jsonb_typeof(t->'lines')<>'array'
           OR EXISTS(SELECT 1 FROM jsonb_array_elements(t->'lines') l
               WHERE jsonb_typeof(l)<>'string'))
       OR EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.document->'charts') c
        WHERE jsonb_typeof(c) IS DISTINCT FROM 'object'
           OR (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(c) k)
               IS DISTINCT FROM ARRAY['axis','kind','last_day','lines','title']
           OR jsonb_typeof(c->'title')<>'string' OR jsonb_typeof(c->'axis')<>'string'
           OR coalesce(c->>'kind','') NOT IN ('count','percent','money')
           OR jsonb_typeof(c->'last_day')<>'number'
           OR jsonb_typeof(c->'lines')<>'array'
           OR EXISTS(SELECT 1 FROM jsonb_array_elements(c->'lines') l
               WHERE jsonb_typeof(l) IS DISTINCT FROM 'object'
                  OR (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(l) k)
                      IS DISTINCT FROM ARRAY['name','points','role']
                  OR jsonb_typeof(l->'name')<>'string'
                  OR coalesce(l->>'role','') NOT IN ('current','last','average','other')
                  OR jsonb_typeof(l->'points')<>'array'
                  OR EXISTS(SELECT 1 FROM jsonb_array_elements(l->'points') p
                      WHERE jsonb_typeof(p) IS DISTINCT FROM 'array'
                         OR jsonb_array_length(p)<>2
                         OR jsonb_typeof(p->0)<>'number'
                         OR jsonb_typeof(p->1) NOT IN ('number','null'))))
    THEN RAISE EXCEPTION 'Invalid comparison export document' USING ERRCODE='23514'; END IF;
    NEW.created_at:=statement_timestamp();
    NEW.row_count:=jsonb_array_length(NEW.document->'rows');
    RETURN NEW;
END $$;

CREATE TRIGGER comparison_export_capture BEFORE INSERT OR UPDATE OR DELETE
    ON stewardship_comparison_export_snapshot FOR EACH ROW
    EXECUTE FUNCTION stewardship_comparison_export_capture_v1();

-- A capture exists only for its export request, by the same requester,
-- campaign and configuration, checked when the transaction commits.
CREATE FUNCTION public.stewardship_comparison_export_binding_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM stewardship_export_request r
        WHERE r.comparison_snapshot_id=NEW.id AND r.requester_id=NEW.actor_id
          AND r.campaign_id=NEW.campaign_id AND r.configuration_id=NEW.configuration_id)
    THEN RAISE EXCEPTION 'Comparison export capture requires its request' USING ERRCODE='23514'; END IF;
    RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER comparison_export_binding AFTER INSERT
    ON stewardship_comparison_export_snapshot DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION stewardship_comparison_export_binding_v1();

-- The request's reference to its capture. A nullable column with no
-- default rewrites no row, and no existing row names a capture.
ALTER TABLE public.stewardship_export_request ADD COLUMN comparison_snapshot_id uuid NULL;
ALTER TABLE public.stewardship_export_request ADD CONSTRAINT export_comparison_snapshot_fk
    FOREIGN KEY(comparison_snapshot_id) REFERENCES stewardship_comparison_export_snapshot(id)
    DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX export_comparison_snapshot ON stewardship_export_request(comparison_snapshot_id);

-- The known report kinds, in the form PostgreSQL deparses the model's
-- declaration: 0063's arms (the latest; family_test_names in CSV, XLSX or
-- PDF and family_timeline with its timeline capture), each now also
-- requiring no comparison capture, plus campaign_comparison, which requires
-- one and no timeline capture. ADD CONSTRAINT validates the existing rows,
-- which all leave the new column NULL.
ALTER TABLE public.stewardship_export_request DROP CONSTRAINT export_report_known;
ALTER TABLE public.stewardship_export_request ADD CONSTRAINT export_report_known CHECK ((((comparison_snapshot_id IS NULL) AND (directory_snapshot_id IS NULL) AND (fact_set_id IS NOT NULL) AND (family_test_names_snapshot_id IS NULL) AND (financial_snapshot_id IS NULL) AND (information_snapshot_id IS NULL) AND (ministry_snapshot_id IS NULL) AND ((report)::text = 'participation'::text) AND (timeline_snapshot_id IS NULL)) OR ((comparison_snapshot_id IS NULL) AND (directory_snapshot_id IS NULL) AND (fact_set_id IS NULL) AND (family_test_names_snapshot_id IS NULL) AND (financial_snapshot_id IS NULL) AND ((format)::text = ANY ((ARRAY['csv'::character varying, 'xlsx'::character varying, 'pdf'::character varying])::text[])) AND (information_snapshot_id IS NOT NULL) AND (ministry_snapshot_id IS NULL) AND ((report)::text = 'additional_information'::text) AND (timeline_snapshot_id IS NULL)) OR ((comparison_snapshot_id IS NULL) AND (directory_snapshot_id IS NOT NULL) AND (fact_set_id IS NULL) AND (family_test_names_snapshot_id IS NULL) AND (financial_snapshot_id IS NULL) AND ((format)::text = ANY ((ARRAY['csv'::character varying, 'xlsx'::character varying, 'pdf'::character varying])::text[])) AND (information_snapshot_id IS NULL) AND (ministry_snapshot_id IS NULL) AND ((report)::text = ANY ((ARRAY['family_directory'::character varying, 'postal_outreach'::character varying])::text[])) AND (timeline_snapshot_id IS NULL)) OR ((comparison_snapshot_id IS NULL) AND (directory_snapshot_id IS NULL) AND (fact_set_id IS NULL) AND (family_test_names_snapshot_id IS NULL) AND (financial_snapshot_id IS NULL) AND ((format)::text = ANY ((ARRAY['csv'::character varying, 'xlsx'::character varying, 'pdf'::character varying])::text[])) AND (information_snapshot_id IS NULL) AND (ministry_snapshot_id IS NOT NULL) AND ((report)::text = 'ministry'::text) AND (timeline_snapshot_id IS NULL)) OR ((comparison_snapshot_id IS NULL) AND (directory_snapshot_id IS NULL) AND (fact_set_id IS NULL) AND (family_test_names_snapshot_id IS NULL) AND (financial_snapshot_id IS NOT NULL) AND ((format)::text = ANY ((ARRAY['csv'::character varying, 'xlsx'::character varying, 'pdf'::character varying])::text[])) AND (information_snapshot_id IS NULL) AND (ministry_snapshot_id IS NULL) AND ((report)::text = 'financial'::text) AND (timeline_snapshot_id IS NULL)) OR ((comparison_snapshot_id IS NULL) AND (directory_snapshot_id IS NULL) AND (fact_set_id IS NULL) AND (family_test_names_snapshot_id IS NOT NULL) AND (financial_snapshot_id IS NULL) AND ((format)::text = ANY ((ARRAY['csv'::character varying, 'xlsx'::character varying, 'pdf'::character varying])::text[])) AND (information_snapshot_id IS NULL) AND (ministry_snapshot_id IS NULL) AND ((report)::text = 'family_test_names'::text) AND (timeline_snapshot_id IS NULL)) OR ((comparison_snapshot_id IS NULL) AND (directory_snapshot_id IS NULL) AND (fact_set_id IS NULL) AND (family_test_names_snapshot_id IS NULL) AND (financial_snapshot_id IS NULL) AND ((format)::text = ANY ((ARRAY['csv'::character varying, 'xlsx'::character varying, 'pdf'::character varying])::text[])) AND (information_snapshot_id IS NULL) AND (ministry_snapshot_id IS NULL) AND ((report)::text = 'family_timeline'::text) AND (timeline_snapshot_id IS NOT NULL)) OR ((comparison_snapshot_id IS NOT NULL) AND (directory_snapshot_id IS NULL) AND (fact_set_id IS NULL) AND (family_test_names_snapshot_id IS NULL) AND (financial_snapshot_id IS NULL) AND ((format)::text = ANY ((ARRAY['csv'::character varying, 'xlsx'::character varying, 'pdf'::character varying])::text[])) AND (information_snapshot_id IS NULL) AND (ministry_snapshot_id IS NULL) AND ((report)::text = 'campaign_comparison'::text) AND (timeline_snapshot_id IS NULL))));

-- The two export guards: their latest bodies (0063's, with its
-- family_timeline branches, which exports.sql carries) plus the comparison
-- branch.
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
               WHERE s.id=request.comparison_snapshot_id AND s.row_count=NEW.row_count)))
       OR NOT EXISTS(SELECT 1 FROM stewardship_export_attempt a JOIN stewardship_task_run t ON t.id=a.run_id
           WHERE a.id=NEW.attempt_id AND a.request_id=request.id AND a.actor_id=NEW.actor_id
             AND t.state='running' AND t.fence=a.fence AND t.worker_id=a.actor_id
             AND t.lease_expires_at>clock_timestamp())
    THEN RAISE EXCEPTION 'Export publication lacks live authorized ownership' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;

-- Refuse to commit unless every part above is installed: the table, its
-- indexes and guards, the column with its key and index, the widened kinds,
-- and the two replaced guards with their comparison branch (and 0037's
-- names and 0063's timeline branches), neither of them SECURITY DEFINER.
-- The constraint is also evaluated against synthetic rows, as 0049's and
-- 0063's checks do: a comparison request in each download format is
-- admitted and a PNG one refused, one without its capture or with another
-- capture (a financial or a timeline one) too is refused, and 0049's names
-- and 0063's timeline formats still hold. No temporary objects: the
-- migration login has no TEMP grant.
DO $check$
DECLARE definition text; expression text; fmt text; admitted boolean; probe json;
BEGIN
    IF to_regclass('public.stewardship_comparison_export_snapshot') IS NULL
       OR to_regclass('public.comparison_export_correlation') IS NULL
       OR to_regclass('public.comparison_export_campaign') IS NULL
       OR to_regclass('public.comparison_export_config') IS NULL
       OR to_regclass('public.export_comparison_snapshot') IS NULL
       OR NOT EXISTS(SELECT 1 FROM pg_attribute
           WHERE attrelid='public.stewardship_export_request'::regclass
             AND attname='comparison_snapshot_id' AND NOT attisdropped AND NOT attnotnull)
       OR NOT EXISTS(SELECT 1 FROM pg_constraint
           WHERE conrelid='public.stewardship_export_request'::regclass
             AND conname='export_comparison_snapshot_fk' AND contype='f'
             AND condeferrable AND condeferred
             AND confrelid='public.stewardship_comparison_export_snapshot'::regclass)
    THEN
        RAISE EXCEPTION 'Migration 0071: the comparison table or request column is incomplete';
    END IF;
    IF (SELECT count(*) FROM pg_trigger t JOIN pg_proc p ON p.oid=t.tgfoid
        WHERE t.tgrelid='public.stewardship_comparison_export_snapshot'::regclass
          AND t.tgenabled='O' AND NOT t.tgisinternal
          AND ((t.tgname='comparison_export_capture'
                AND p.proname='stewardship_comparison_export_capture_v1'
                AND t.tgtype=31)
            OR (t.tgname='comparison_export_binding'
                AND p.proname='stewardship_comparison_export_binding_v1'
                AND t.tgconstraint<>0 AND t.tgdeferrable AND t.tginitdeferred)))<>2
    THEN
        RAISE EXCEPTION 'Migration 0071: the comparison capture guards are not installed';
    END IF;
    SELECT pg_get_constraintdef(oid) INTO definition FROM pg_constraint
     WHERE conrelid='public.stewardship_export_request'::regclass
       AND conname='export_report_known' AND contype='c' AND convalidated;
    IF definition IS NULL
       OR position('''campaign_comparison''::text' IN definition)=0
       OR position('''family_test_names''::text' IN definition)=0
       OR position('''family_timeline''::text' IN definition)=0
       OR (length(definition)-length(replace(definition,'comparison_snapshot_id IS NULL','')))
          /length('comparison_snapshot_id IS NULL')<>7
       OR (length(definition)-length(replace(definition,'family_test_names_snapshot_id IS NULL','')))
          /length('family_test_names_snapshot_id IS NULL')<>7
       OR (length(definition)-length(replace(definition,'timeline_snapshot_id IS NULL','')))
          /length('timeline_snapshot_id IS NULL')<>7
    THEN
        RAISE EXCEPTION 'Migration 0071: export_report_known does not admit the comparison kind';
    END IF;
    SELECT pg_get_expr(conbin, conrelid) INTO expression FROM pg_constraint
     WHERE conrelid='public.stewardship_export_request'::regclass
       AND conname='export_report_known' AND contype='c' AND convalidated;
    FOREACH fmt IN ARRAY ARRAY['csv','xlsx','pdf','png'] LOOP
        FOR probe IN SELECT unnest(ARRAY[
            json_build_object('report','campaign_comparison','format',fmt,
                'comparison_snapshot_id','00000000-0000-4000-8000-000000000000'),
            json_build_object('report','family_test_names','format',fmt,
                'family_test_names_snapshot_id','00000000-0000-4000-8000-000000000000'),
            json_build_object('report','family_timeline','format',fmt,
                'timeline_snapshot_id','00000000-0000-4000-8000-000000000000')])
        LOOP
            EXECUTE format(
                'SELECT %s FROM json_populate_record(NULL::public.stewardship_export_request, $1) AS stewardship_export_request',
                expression) INTO admitted USING probe;
            IF admitted IS DISTINCT FROM (fmt<>'png') THEN
                RAISE EXCEPTION 'Migration 0071: export_report_known does not admit the comparison kind';
            END IF;
        END LOOP;
    END LOOP;
    -- A comparison request needs its capture, and only its capture.
    FOR probe IN SELECT unnest(ARRAY[
        json_build_object('report','campaign_comparison','format','csv'),
        json_build_object('report','campaign_comparison','format','csv',
            'comparison_snapshot_id','00000000-0000-4000-8000-000000000000',
            'financial_snapshot_id','00000000-0000-4000-8000-000000000000'),
        json_build_object('report','financial','format','csv',
            'comparison_snapshot_id','00000000-0000-4000-8000-000000000000',
            'financial_snapshot_id','00000000-0000-4000-8000-000000000000'),
        json_build_object('report','campaign_comparison','format','csv',
            'comparison_snapshot_id','00000000-0000-4000-8000-000000000000',
            'timeline_snapshot_id','00000000-0000-4000-8000-000000000000'),
        json_build_object('report','family_timeline','format','csv',
            'comparison_snapshot_id','00000000-0000-4000-8000-000000000000',
            'timeline_snapshot_id','00000000-0000-4000-8000-000000000000')])
    LOOP
        EXECUTE format(
            'SELECT %s FROM json_populate_record(NULL::public.stewardship_export_request, $1) AS stewardship_export_request',
            expression) INTO admitted USING probe;
        IF admitted IS DISTINCT FROM false THEN
            RAISE EXCEPTION 'Migration 0071: export_report_known does not admit the comparison kind';
        END IF;
    END LOOP;
    IF (SELECT count(*) FROM pg_proc
        WHERE pronamespace='public'::regnamespace
          AND proname IN ('stewardship_export_request_guard_v1',
                          'stewardship_export_publication_guard_v1')
          AND position('stewardship_comparison_export_snapshot' IN prosrc)>0
          AND position('stewardship_family_test_names_export_snapshot' IN prosrc)>0
          AND position('stewardship_timeline_export_snapshot' IN prosrc)>0
          AND NOT prosecdef)<>2
    THEN
        RAISE EXCEPTION 'Migration 0071: the export guards were not replaced';
    END IF;
END
$check$;
