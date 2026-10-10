-- Frozen forward migration 0044: the Family timeline export (ADM-11 PR 8g, #463).
-- It is installed by the Django migration that names it in FROZEN_SQL and
-- must never change once released; tests/stewardship/test_schema_migration_files.py
-- pins its digest and checks that its copies of the replaced functions equal
-- the fresh-install baseline's (schema/exports.sql, schema/ministry_exports.sql).
--
-- One Family's timeline, as its Administrator page shows it, becomes a report
-- export kind, family_timeline: the web captures the page's own read into an
-- immutable stewardship_timeline_export_snapshot, and the shared export
-- lifecycle renders, publishes and downloads it. The capture's document comes
-- from the web (the page builds the timeline in Python); this file checks
-- everything around it. The Family code is never stored.
--
-- New, migration-owned: the snapshot table, its capture and binding triggers.
-- Changed: stewardship_export_request gains timeline_snapshot_id, and its
-- export_report_known check gains a family_timeline arm, every earlier arm
-- (0037's list, including family_test_names) also requiring no timeline
-- capture. Three functions are re-created from their latest bodies plus only
-- the timeline branch: stewardship_export_request_guard_v1 and
-- stewardship_export_publication_guard_v1 (last replaced by 0037) and
-- stewardship_export_request_authorized_v1 (the baseline's, never replaced
-- before). None of the three is SECURITY DEFINER in the baseline, CREATE OR
-- REPLACE keeps that, and the closing check confirms it. Grants follow from
-- reports/export_grants.py through the upgrade's database-grants step. No
-- existing row changes. Reversing needs its own forward migration.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

CREATE TABLE stewardship_timeline_export_snapshot (
    id uuid PRIMARY KEY, created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
    actor_id uuid NOT NULL, correlation_id uuid NOT NULL,
    campaign_id uuid NOT NULL REFERENCES stewardship_campaign(id) DEFERRABLE INITIALLY DEFERRED,
    family_id uuid NOT NULL REFERENCES stewardship_family_campaign(id) DEFERRABLE INITIALLY DEFERRED,
    configuration_id uuid NOT NULL REFERENCES stewardship_configuration_version(id) DEFERRABLE INITIALLY DEFERRED,
    parameters jsonb NOT NULL, document jsonb NOT NULL,
    row_count integer NOT NULL CHECK(row_count>=0)
);
CREATE INDEX timeline_export_correlation ON stewardship_timeline_export_snapshot(correlation_id);
CREATE INDEX timeline_export_campaign ON stewardship_timeline_export_snapshot(campaign_id);
CREATE INDEX timeline_export_family ON stewardship_timeline_export_snapshot(family_id);
CREATE INDEX timeline_export_config ON stewardship_timeline_export_snapshot(configuration_id);

-- The web's capture is admitted only for a current Administrator (the page's
-- full view), an admitted campaign under its export lock, the active
-- configuration, a Family of that campaign, coherent parameters and the
-- document's closed shape. The trigger sets the time and the event count.
CREATE FUNCTION public.stewardship_timeline_export_capture_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE event jsonb;
BEGIN
    IF TG_OP<>'INSERT' THEN
        RAISE EXCEPTION 'Timeline export snapshots are immutable' USING ERRCODE='23514';
    END IF;
    PERFORM stewardship_export_campaign_lock_v1(NEW.campaign_id,false);
    IF current_user='pk_stewardship_worker'
       OR NOT stewardship_export_authorized_v1(NEW.actor_id,true)
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
           IS DISTINCT FROM ARRAY['duid','envelope','id','name','reach']
       OR NEW.document->'family'->>'id' IS DISTINCT FROM NEW.family_id::text
       OR jsonb_typeof(NEW.document->'family'->'duid') IS DISTINCT FROM 'number'
       OR NOT EXISTS(SELECT 1 FROM stewardship_family_campaign f WHERE f.id=NEW.family_id
           AND f.family_duid::text=NEW.document->'family'->>'duid')
       OR jsonb_typeof(NEW.document->'family'->'name') NOT IN ('string','null')
       OR jsonb_typeof(NEW.document->'family'->'envelope') NOT IN ('number','null')
       OR jsonb_typeof(NEW.document->'family'->'reach') IS DISTINCT FROM 'string'
       -- One Family's timeline is small; a bound keeps a capture from
       -- growing without limit, as the Ministry capture's search bound does.
       OR octet_length(NEW.document::text)>131072
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

CREATE TRIGGER timeline_export_capture BEFORE INSERT OR UPDATE OR DELETE
    ON stewardship_timeline_export_snapshot FOR EACH ROW
    EXECUTE FUNCTION stewardship_timeline_export_capture_v1();

CREATE FUNCTION public.stewardship_timeline_export_binding_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM stewardship_export_request r
        WHERE r.timeline_snapshot_id=NEW.id AND r.requester_id=NEW.actor_id
          AND r.campaign_id=NEW.campaign_id AND r.configuration_id=NEW.configuration_id)
    THEN RAISE EXCEPTION 'Timeline export capture requires its request' USING ERRCODE='23514'; END IF;
    RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER timeline_export_binding AFTER INSERT
    ON stewardship_timeline_export_snapshot DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION stewardship_timeline_export_binding_v1();

ALTER TABLE stewardship_export_request ADD COLUMN timeline_snapshot_id uuid NULL;
ALTER TABLE stewardship_export_request ADD CONSTRAINT export_timeline_snapshot_fk
    FOREIGN KEY(timeline_snapshot_id) REFERENCES stewardship_timeline_export_snapshot(id)
    DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX export_timeline_snapshot ON stewardship_export_request(timeline_snapshot_id);

-- The known report kinds, in the form PostgreSQL deparses the model's
-- declaration: 0037's arms (the latest), each now also requiring no timeline
-- capture, plus family_timeline, which requires one. ADD CONSTRAINT
-- validates the existing rows, which all leave the new column NULL.
ALTER TABLE public.stewardship_export_request DROP CONSTRAINT export_report_known;
ALTER TABLE public.stewardship_export_request ADD CONSTRAINT export_report_known CHECK ((((directory_snapshot_id IS NULL) AND (fact_set_id IS NOT NULL) AND (family_test_names_snapshot_id IS NULL) AND (financial_snapshot_id IS NULL) AND (information_snapshot_id IS NULL) AND (ministry_snapshot_id IS NULL) AND ((report)::text = 'participation'::text) AND (timeline_snapshot_id IS NULL)) OR ((directory_snapshot_id IS NULL) AND (fact_set_id IS NULL) AND (family_test_names_snapshot_id IS NULL) AND (financial_snapshot_id IS NULL) AND ((format)::text = ANY ((ARRAY['csv'::character varying, 'xlsx'::character varying, 'pdf'::character varying])::text[])) AND (information_snapshot_id IS NOT NULL) AND (ministry_snapshot_id IS NULL) AND ((report)::text = 'additional_information'::text) AND (timeline_snapshot_id IS NULL)) OR ((directory_snapshot_id IS NOT NULL) AND (fact_set_id IS NULL) AND (family_test_names_snapshot_id IS NULL) AND (financial_snapshot_id IS NULL) AND ((format)::text = ANY ((ARRAY['csv'::character varying, 'xlsx'::character varying, 'pdf'::character varying])::text[])) AND (information_snapshot_id IS NULL) AND (ministry_snapshot_id IS NULL) AND ((report)::text = ANY ((ARRAY['family_directory'::character varying, 'postal_outreach'::character varying])::text[])) AND (timeline_snapshot_id IS NULL)) OR ((directory_snapshot_id IS NULL) AND (fact_set_id IS NULL) AND (family_test_names_snapshot_id IS NULL) AND (financial_snapshot_id IS NULL) AND ((format)::text = ANY ((ARRAY['csv'::character varying, 'xlsx'::character varying, 'pdf'::character varying])::text[])) AND (information_snapshot_id IS NULL) AND (ministry_snapshot_id IS NOT NULL) AND ((report)::text = 'ministry'::text) AND (timeline_snapshot_id IS NULL)) OR ((directory_snapshot_id IS NULL) AND (fact_set_id IS NULL) AND (family_test_names_snapshot_id IS NULL) AND (financial_snapshot_id IS NOT NULL) AND ((format)::text = ANY ((ARRAY['csv'::character varying, 'xlsx'::character varying, 'pdf'::character varying])::text[])) AND (information_snapshot_id IS NULL) AND (ministry_snapshot_id IS NULL) AND ((report)::text = 'financial'::text) AND (timeline_snapshot_id IS NULL)) OR ((directory_snapshot_id IS NULL) AND (fact_set_id IS NULL) AND (family_test_names_snapshot_id IS NOT NULL) AND (financial_snapshot_id IS NULL) AND ((format)::text = 'csv'::text) AND (information_snapshot_id IS NULL) AND (ministry_snapshot_id IS NULL) AND ((report)::text = 'family_test_names'::text) AND (timeline_snapshot_id IS NULL)) OR ((directory_snapshot_id IS NULL) AND (fact_set_id IS NULL) AND (family_test_names_snapshot_id IS NULL) AND (financial_snapshot_id IS NULL) AND ((format)::text = ANY ((ARRAY['csv'::character varying, 'xlsx'::character varying, 'pdf'::character varying])::text[])) AND (information_snapshot_id IS NULL) AND (ministry_snapshot_id IS NULL) AND ((report)::text = 'family_timeline'::text) AND (timeline_snapshot_id IS NOT NULL))));

CREATE OR REPLACE FUNCTION public.stewardship_export_request_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE facts stewardship_daily_fact_set%ROWTYPE;
        snapshot stewardship_information_export_snapshot%ROWTYPE;
        directory stewardship_directory_export_snapshot%ROWTYPE;
        ministry stewardship_ministry_export_snapshot%ROWTYPE;
        financial stewardship_financial_export_snapshot%ROWTYPE;
        names stewardship_family_test_names_export_snapshot%ROWTYPE;
        timeline stewardship_timeline_export_snapshot%ROWTYPE;
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
        -- One Family's timeline (migration 0044): Administrator only, as
        -- the page's full view, so any current Administrator may also
        -- regenerate another's capture.
        SELECT * INTO timeline FROM stewardship_timeline_export_snapshot WHERE id=NEW.timeline_snapshot_id;
        inputs_valid:=timeline.id IS NOT NULL AND timeline.campaign_id=NEW.campaign_id
            AND NEW.parameters=timeline.parameters
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
               WHERE s.id=request.timeline_snapshot_id AND s.row_count=NEW.row_count)))
       OR NOT EXISTS(SELECT 1 FROM stewardship_export_attempt a JOIN stewardship_task_run t ON t.id=a.run_id
           WHERE a.id=NEW.attempt_id AND a.request_id=request.id AND a.actor_id=NEW.actor_id
             AND t.state='running' AND t.fence=a.fence AND t.worker_id=a.actor_id
             AND t.lease_expires_at>clock_timestamp())
    THEN RAISE EXCEPTION 'Export publication lacks live authorized ownership' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION public.stewardship_export_request_authorized_v1(user_uuid uuid, request stewardship_export_request)
RETURNS boolean LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT (user_uuid=request.requester_id OR stewardship_export_authorized_v1(user_uuid,true))
        AND CASE WHEN request.report='ministry' THEN
            stewardship_ministry_scope_authorized_v1(user_uuid,request.authorization_scope)
        WHEN request.report='family_timeline' THEN
            -- One Family's full timeline is the page's Administrator view
            -- (migration 0044), for the requester as for anyone else.
            stewardship_export_authorized_v1(user_uuid,true)
        ELSE stewardship_export_authorized_v1(user_uuid) END
$$;

-- Refuse to commit unless every part above is installed: the table, its
-- indexes and guards, the column with its key and index, the widened kinds,
-- and the three replaced functions with their timeline branch (and, for the
-- two guards, 0037's names branch), none of them SECURITY DEFINER. No
-- temporary objects: the migration login has no TEMP grant.
DO $check$
DECLARE definition text;
BEGIN
    IF to_regclass('public.stewardship_timeline_export_snapshot') IS NULL
       OR to_regclass('public.timeline_export_correlation') IS NULL
       OR to_regclass('public.timeline_export_campaign') IS NULL
       OR to_regclass('public.timeline_export_family') IS NULL
       OR to_regclass('public.timeline_export_config') IS NULL
       OR to_regclass('public.export_timeline_snapshot') IS NULL
       OR NOT EXISTS(SELECT 1 FROM pg_attribute
           WHERE attrelid='public.stewardship_export_request'::regclass
             AND attname='timeline_snapshot_id' AND NOT attisdropped AND NOT attnotnull)
       OR NOT EXISTS(SELECT 1 FROM pg_constraint
           WHERE conrelid='public.stewardship_export_request'::regclass
             AND conname='export_timeline_snapshot_fk' AND contype='f'
             AND condeferrable AND condeferred
             AND confrelid='public.stewardship_timeline_export_snapshot'::regclass)
    THEN
        RAISE EXCEPTION 'Migration 0044: the timeline table or request column is incomplete';
    END IF;
    IF (SELECT count(*) FROM pg_trigger t JOIN pg_proc p ON p.oid=t.tgfoid
        WHERE t.tgrelid='public.stewardship_timeline_export_snapshot'::regclass
          AND t.tgenabled='O' AND NOT t.tgisinternal
          AND ((t.tgname='timeline_export_capture'
                AND p.proname='stewardship_timeline_export_capture_v1'
                AND t.tgtype=31)
            OR (t.tgname='timeline_export_binding'
                AND p.proname='stewardship_timeline_export_binding_v1'
                AND t.tgconstraint<>0 AND t.tgdeferrable AND t.tginitdeferred)))<>2
    THEN
        RAISE EXCEPTION 'Migration 0044: the timeline capture guards are not installed';
    END IF;
    SELECT pg_get_constraintdef(oid) INTO definition FROM pg_constraint
     WHERE conrelid='public.stewardship_export_request'::regclass
       AND conname='export_report_known' AND contype='c' AND convalidated;
    IF definition IS NULL
       OR position('''family_timeline''::text' IN definition)=0
       OR position('''family_test_names''::text' IN definition)=0
       OR (length(definition)-length(replace(definition,'timeline_snapshot_id IS NULL','')))
          /length('timeline_snapshot_id IS NULL')<>6
       OR (length(definition)-length(replace(definition,'family_test_names_snapshot_id IS NULL','')))
          /length('family_test_names_snapshot_id IS NULL')<>6
    THEN
        RAISE EXCEPTION 'Migration 0044: export_report_known does not admit the timeline kind';
    END IF;
    IF (SELECT count(*) FROM pg_proc
        WHERE pronamespace='public'::regnamespace
          AND proname IN ('stewardship_export_request_guard_v1',
                          'stewardship_export_publication_guard_v1')
          AND position('stewardship_timeline_export_snapshot' IN prosrc)>0
          AND position('stewardship_family_test_names_export_snapshot' IN prosrc)>0
          AND NOT prosecdef)<>2
       OR NOT EXISTS(SELECT 1 FROM pg_proc
           WHERE pronamespace='public'::regnamespace
             AND proname='stewardship_export_request_authorized_v1'
             AND position('family_timeline' IN prosrc)>0
             AND position('stewardship_ministry_scope_authorized_v1' IN prosrc)>0
             AND NOT prosecdef)
    THEN
        RAISE EXCEPTION 'Migration 0044: the export guards were not replaced';
    END IF;
END
$check$;
