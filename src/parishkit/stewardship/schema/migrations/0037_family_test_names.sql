-- Frozen forward migration file 0037 (the repository-wide file sequence):
-- the chosen-Family test names export (#817). This file is installed by its
-- Django migration in the reports app and must never change once released;
-- tests/stewardship/test_schema_migration_files.py pins its digest and checks
-- that its copies of the two replaced export guards still equal the
-- fresh-install baseline's (schema/exports.sql). A later change gets its own
-- numbered file. A fresh install runs the baseline, the earlier files and
-- then this; the baseline already carries the two guards' new bodies, and
-- this file adds the table, the column, the constraint and the guards'
-- copies, so the install ends in the same catalog as an upgraded database.
--
-- pk-admin test families-preview --names keeps Family names off the
-- terminal: it captures the names the review read into
-- stewardship_family_test_names_export_snapshot and requests a CSV export of
-- kind family_test_names through the shared export lifecycle, which the
-- worker renders and export fetch downloads. This file:
--   - creates the capture table, as Django renders FamilyTestNamesSnapshot,
--     with its capture guard (insert only, by an Administrator, for the
--     current Testing draft, one {"duid","name"} row per reviewed DUID in
--     order) and its deferred binding to an export request;
--   - adds stewardship_export_request.family_test_names_snapshot_id with its
--     deferrable foreign key and index;
--   - replaces export_report_known with 0001's list (exports.sql; no later
--     file changed it) plus the family_test_names arm (CSV only), every
--     earlier arm also requiring the new column to be NULL, in the form
--     PostgreSQL deparses Django's declaration;
--   - replaces stewardship_export_request_guard_v1 and
--     stewardship_export_publication_guard_v1 with the baseline's bodies
--     (exports.sql, unchanged since 0001 until now) plus one branch each
--     for the new kind.
-- Neither replaced guard is SECURITY DEFINER in the baseline, and CREATE OR
-- REPLACE keeps that; the check at the end confirms it. Grants follow from
-- reports/export_grants.py through the upgrade's database-grants step. No
-- existing row changes. Reversing needs its own forward migration.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

-- The capture table, as Django renders the model.
CREATE TABLE "stewardship_family_test_names_export_snapshot" ("id" uuid NOT NULL PRIMARY KEY, "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "campaign_id" uuid NOT NULL, "configuration_id" uuid NOT NULL, "actor_id" uuid NOT NULL, "correlation_id" uuid NOT NULL, "parameters" jsonb NOT NULL, "document" jsonb NOT NULL, "row_count" integer NOT NULL CHECK ("row_count" >= 0));
ALTER TABLE "stewardship_family_test_names_export_snapshot" ADD CONSTRAINT "stewardship_family_t_campaign_id_a214c950_fk_stewardsh" FOREIGN KEY ("campaign_id") REFERENCES "stewardship_campaign" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_family_test_names_export_snapshot" ADD CONSTRAINT "stewardship_family_t_configuration_id_9f944015_fk_stewardsh" FOREIGN KEY ("configuration_id") REFERENCES "stewardship_configuration_version" ("id") DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX "family_test_names_correlation" ON "stewardship_family_test_names_export_snapshot" ("correlation_id");
CREATE INDEX "family_test_names_campaign" ON "stewardship_family_test_names_export_snapshot" ("campaign_id");
CREATE INDEX "family_test_names_config" ON "stewardship_family_test_names_export_snapshot" ("configuration_id");

-- Only an Administrator (the page needs the configure capability, which
-- only an Administrator holds) captures names, from the web login, for the
-- current Testing draft under the active configuration, while the campaign
-- admits exports. The capture is the review's: parameters are exactly the
-- email revision and one to ten distinct positive DUIDs in the review's
-- order, and the document is exactly one {"duid","name"} row per DUID in
-- that order. The names themselves come from the review's own read; SQL
-- cannot recompute the page's naming rule. Rows never change.
CREATE FUNCTION public.stewardship_family_test_names_export_capture_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE chosen jsonb; captured jsonb;
BEGIN
    IF TG_OP<>'INSERT' THEN
        RAISE EXCEPTION 'Chosen-Family name captures are immutable' USING ERRCODE='23514';
    END IF;
    PERFORM stewardship_export_campaign_lock_v1(NEW.campaign_id,false);
    IF NOT stewardship_export_authorized_v1(NEW.actor_id,true)
       OR NOT stewardship_export_admitted_v1(NEW.campaign_id,true)
       OR current_user='pk_stewardship_worker'
       OR NOT EXISTS(SELECT 1 FROM stewardship_system_configuration s
           JOIN stewardship_campaign c ON c.id=s.current_campaign_id
           WHERE s.active_configuration_id=NEW.configuration_id AND s.mode='testing'
             AND c.id=NEW.campaign_id AND c.state='draft')
    THEN RAISE EXCEPTION 'Chosen-Family name capture is unavailable' USING ERRCODE='23514'; END IF;
    chosen:=NEW.parameters->'duids';
    captured:=NEW.document->'rows';
    -- The JSON types first, so the checks below never apply an object or
    -- array function to the wrong type.
    IF jsonb_typeof(NEW.parameters) IS DISTINCT FROM 'object'
       OR jsonb_typeof(NEW.document) IS DISTINCT FROM 'object'
       OR jsonb_typeof(chosen) IS DISTINCT FROM 'array'
       OR jsonb_typeof(captured) IS DISTINCT FROM 'array'
    THEN RAISE EXCEPTION 'Chosen-Family name capture does not match its review'
        USING ERRCODE='23514'; END IF;
    IF (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(NEW.parameters) k)
           IS DISTINCT FROM ARRAY['duids','revision']
       OR (SELECT array_agg(k) FROM jsonb_object_keys(NEW.document) k)
           IS DISTINCT FROM ARRAY['rows']
       OR coalesce(NEW.parameters->>'revision','')
           !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
       OR jsonb_array_length(chosen) NOT BETWEEN 1 AND 10
       OR jsonb_array_length(captured)<>jsonb_array_length(chosen)
       OR NEW.row_count<>jsonb_array_length(chosen)
       OR (SELECT count(DISTINCT d) FROM jsonb_array_elements(chosen) d)<>jsonb_array_length(chosen)
       OR EXISTS(SELECT 1 FROM jsonb_array_elements(chosen) WITH ORDINALITY d(duid,n)
           LEFT JOIN jsonb_array_elements(captured) WITH ORDINALITY r(entry,n) ON r.n=d.n
           WHERE d.duid::text !~ '^[1-9][0-9]{0,17}$'
              OR CASE WHEN jsonb_typeof(r.entry)='object'
                 THEN r.entry-'duid'-'name'<>'{}'::jsonb
                      OR r.entry->'duid' IS DISTINCT FROM d.duid
                      OR jsonb_typeof(r.entry->'name') IS DISTINCT FROM 'string'
                 ELSE true END)
    THEN RAISE EXCEPTION 'Chosen-Family name capture does not match its review'
        USING ERRCODE='23514'; END IF;
    NEW.created_at:=statement_timestamp();
    RETURN NEW;
END $$;
CREATE TRIGGER family_test_names_export_capture BEFORE INSERT OR UPDATE OR DELETE
    ON stewardship_family_test_names_export_snapshot FOR EACH ROW
    EXECUTE FUNCTION stewardship_family_test_names_export_capture_v1();

-- A capture exists only for its export request, made in the same
-- transaction by the same Administrator under the same configuration.
CREATE FUNCTION public.stewardship_family_test_names_export_binding_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM stewardship_export_request r
        WHERE r.family_test_names_snapshot_id=NEW.id AND r.requester_id=NEW.actor_id
          AND r.campaign_id=NEW.campaign_id AND r.configuration_id=NEW.configuration_id)
    THEN RAISE EXCEPTION 'Chosen-Family name capture requires its request' USING ERRCODE='23514'; END IF;
    RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER family_test_names_export_binding AFTER INSERT
    ON stewardship_family_test_names_export_snapshot DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION stewardship_family_test_names_export_binding_v1();

-- The request's reference to its capture. A nullable column with no
-- default rewrites no row, and no existing row names a capture.
ALTER TABLE public.stewardship_export_request ADD COLUMN family_test_names_snapshot_id uuid NULL;
ALTER TABLE public.stewardship_export_request ADD CONSTRAINT export_family_test_names_snapshot_fk
    FOREIGN KEY(family_test_names_snapshot_id) REFERENCES stewardship_family_test_names_export_snapshot(id)
    DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX export_family_test_names ON stewardship_export_request(family_test_names_snapshot_id);

-- The known report kinds: 0001's arms, each now also requiring no names
-- capture, plus the names kind (CSV only). ADD CONSTRAINT validates the
-- existing rows, which all leave the new column NULL.
ALTER TABLE public.stewardship_export_request DROP CONSTRAINT export_report_known;
ALTER TABLE public.stewardship_export_request ADD CONSTRAINT export_report_known CHECK ((((directory_snapshot_id IS NULL) AND (fact_set_id IS NOT NULL) AND (family_test_names_snapshot_id IS NULL) AND (financial_snapshot_id IS NULL) AND (information_snapshot_id IS NULL) AND (ministry_snapshot_id IS NULL) AND ((report)::text = 'participation'::text)) OR ((directory_snapshot_id IS NULL) AND (fact_set_id IS NULL) AND (family_test_names_snapshot_id IS NULL) AND (financial_snapshot_id IS NULL) AND ((format)::text = ANY ((ARRAY['csv'::character varying, 'xlsx'::character varying, 'pdf'::character varying])::text[])) AND (information_snapshot_id IS NOT NULL) AND (ministry_snapshot_id IS NULL) AND ((report)::text = 'additional_information'::text)) OR ((directory_snapshot_id IS NOT NULL) AND (fact_set_id IS NULL) AND (family_test_names_snapshot_id IS NULL) AND (financial_snapshot_id IS NULL) AND ((format)::text = ANY ((ARRAY['csv'::character varying, 'xlsx'::character varying, 'pdf'::character varying])::text[])) AND (information_snapshot_id IS NULL) AND (ministry_snapshot_id IS NULL) AND ((report)::text = ANY ((ARRAY['family_directory'::character varying, 'postal_outreach'::character varying])::text[]))) OR ((directory_snapshot_id IS NULL) AND (fact_set_id IS NULL) AND (family_test_names_snapshot_id IS NULL) AND (financial_snapshot_id IS NULL) AND ((format)::text = ANY ((ARRAY['csv'::character varying, 'xlsx'::character varying, 'pdf'::character varying])::text[])) AND (information_snapshot_id IS NULL) AND (ministry_snapshot_id IS NOT NULL) AND ((report)::text = 'ministry'::text)) OR ((directory_snapshot_id IS NULL) AND (fact_set_id IS NULL) AND (family_test_names_snapshot_id IS NULL) AND (financial_snapshot_id IS NOT NULL) AND ((format)::text = ANY ((ARRAY['csv'::character varying, 'xlsx'::character varying, 'pdf'::character varying])::text[])) AND (information_snapshot_id IS NULL) AND (ministry_snapshot_id IS NULL) AND ((report)::text = 'financial'::text)) OR ((directory_snapshot_id IS NULL) AND (fact_set_id IS NULL) AND (family_test_names_snapshot_id IS NOT NULL) AND (financial_snapshot_id IS NULL) AND ((format)::text = 'csv'::text) AND (information_snapshot_id IS NULL) AND (ministry_snapshot_id IS NULL) AND ((report)::text = 'family_test_names'::text))));

-- The two export guards, exactly as the fresh-install exports.sql defines them.
CREATE OR REPLACE FUNCTION public.stewardship_export_request_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE facts stewardship_daily_fact_set%ROWTYPE;
        snapshot stewardship_information_export_snapshot%ROWTYPE;
        directory stewardship_directory_export_snapshot%ROWTYPE;
        ministry stewardship_ministry_export_snapshot%ROWTYPE;
        financial stewardship_financial_export_snapshot%ROWTYPE;
        names stewardship_family_test_names_export_snapshot%ROWTYPE;
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
               WHERE s.id=request.family_test_names_snapshot_id AND s.row_count=NEW.row_count)))
       OR NOT EXISTS(SELECT 1 FROM stewardship_export_attempt a JOIN stewardship_task_run t ON t.id=a.run_id
           WHERE a.id=NEW.attempt_id AND a.request_id=request.id AND a.actor_id=NEW.actor_id
             AND t.state='running' AND t.fence=a.fence AND t.worker_id=a.actor_id
             AND t.lease_expires_at>clock_timestamp())
    THEN RAISE EXCEPTION 'Export publication lacks live authorized ownership' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;

-- Refuse to commit unless every part above is installed: the table and its
-- guards, the column with its key and index, the widened kinds, and both
-- replaced guards with their new branch, still not SECURITY DEFINER. No
-- temporary objects: the migration login has no TEMP grant.
DO $check$
DECLARE definition text;
BEGIN
    IF to_regclass('public.stewardship_family_test_names_export_snapshot') IS NULL
       OR to_regclass('public.export_family_test_names') IS NULL
       OR NOT EXISTS(SELECT 1 FROM pg_attribute
           WHERE attrelid='public.stewardship_export_request'::regclass
             AND attname='family_test_names_snapshot_id' AND NOT attisdropped
             AND NOT attnotnull)
       OR NOT EXISTS(SELECT 1 FROM pg_constraint
           WHERE conrelid='public.stewardship_export_request'::regclass
             AND conname='export_family_test_names_snapshot_fk' AND contype='f'
             AND condeferrable AND condeferred
             AND confrelid='public.stewardship_family_test_names_export_snapshot'::regclass)
    THEN
        RAISE EXCEPTION 'The chosen-Family names table or request column is incomplete';
    END IF;
    IF (SELECT count(*) FROM pg_trigger t JOIN pg_proc p ON p.oid=t.tgfoid
        WHERE t.tgrelid='public.stewardship_family_test_names_export_snapshot'::regclass
          AND t.tgenabled='O' AND NOT t.tgisinternal
          AND ((t.tgname='family_test_names_export_capture'
                AND p.proname='stewardship_family_test_names_export_capture_v1'
                AND t.tgtype=31)
            OR (t.tgname='family_test_names_export_binding'
                AND p.proname='stewardship_family_test_names_export_binding_v1'
                AND t.tgconstraint<>0)))<>2
    THEN
        RAISE EXCEPTION 'The chosen-Family names guards are not installed';
    END IF;
    SELECT pg_get_constraintdef(oid) INTO definition FROM pg_constraint
     WHERE conrelid='public.stewardship_export_request'::regclass
       AND conname='export_report_known' AND contype='c' AND convalidated;
    IF definition IS NULL
       OR position('''family_test_names''::text' IN definition)=0
       OR (length(definition)-length(replace(definition,'family_test_names_snapshot_id IS NULL','')))
          /length('family_test_names_snapshot_id IS NULL')<>5
    THEN
        RAISE EXCEPTION 'export_report_known does not admit the names kind';
    END IF;
    IF (SELECT count(*) FROM pg_proc
        WHERE pronamespace='public'::regnamespace
          AND proname IN ('stewardship_export_request_guard_v1',
                          'stewardship_export_publication_guard_v1')
          AND position('family_test_names_export_snapshot' IN prosrc)>0
          AND NOT prosecdef)<>2
    THEN
        RAISE EXCEPTION 'The export guards were not replaced';
    END IF;
END
$check$;
