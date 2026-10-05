-- Frozen forward migration file 0007 (the repository-wide file sequence;
-- Django's stewardship_jobs.0007): the System health page's records (ADM-13
-- PR 1, #530). This file is installed by
-- jobs/migrations/0007_system_health_records.py and must never change once
-- released; tests/stewardship/test_schema_migration_files.py pins its digest.
-- A later change gets its own numbered file. A fresh install runs the
-- baseline, 0002 to 0006 and then this, and ends in the same catalog as an
-- upgraded database. It replaces one existing object: the
-- operational_event_safe constraint, whose final text is in tables.sql.
--
-- stewardship_service_status: what each online process (web, worker and its
-- source process, scheduler, each mail consumer, the configuration installer
-- and each credential installer) last reported about itself: version, debug
-- logging, and a mail consumer's sender state. Each login writes only its own
-- service's rows, every time comes from the database clock, and only the
-- worker's housekeeping deletes a row, a day after its last report. The rows
-- are for display: no sending, refresh or backup decision reads them.
--
-- stewardship_source_drop_count: every count a refused ParishSoft load was
-- checked on, written by the worker with the refusal (counts only, never
-- parish data). Rows are append-only and never deleted.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

CREATE TABLE "stewardship_service_status" ("id" uuid NOT NULL PRIMARY KEY, "service" varchar(24) NOT NULL, "process" varchar(8) NOT NULL, "target" varchar(32) NULL, "started_at" timestamp with time zone NOT NULL, "reported_at" timestamp with time zone NOT NULL, "application_version" varchar(40) NOT NULL, "debug_logging" boolean NOT NULL, "sender_state" varchar(16) NULL, "sender_since" timestamp with time zone NULL, "sender_until" timestamp with time zone NULL, CONSTRAINT "service_status_process" CHECK ((("process" = 'main' OR ("process" = 'source' AND "service" = 'worker') OR ("process" = 'mail' AND "service" = 'mail-dispatch')) AND (("service" = 'credential-installer' AND "target" IS NOT NULL) OR (NOT ("service" = 'credential-installer') AND "target" IS NULL)))), CONSTRAINT "service_status_target" CHECK (("target" IS NULL OR "target"::text ~ '^[a-z][a-z0-9_]{0,31}$')), CONSTRAINT "service_status_version" CHECK ("application_version"::text ~ '^[0-9A-Za-z.+-]{1,40}$'), CONSTRAINT "service_status_times" CHECK ("started_at" <= ("reported_at")));
-- The closed name lists in the form Django's own ALTER TABLE renders them,
-- so the installed constraints equal the model declarations.
ALTER TABLE "stewardship_service_status" ADD CONSTRAINT "service_status_names" CHECK ((((service)::text = ANY (ARRAY[('web'::character varying)::text, ('worker'::character varying)::text, ('scheduler'::character varying)::text, ('mail-dispatch'::character varying)::text, ('config-installer'::character varying)::text, ('credential-installer'::character varying)::text])) AND ((process)::text = ANY (ARRAY[('main'::character varying)::text, ('source'::character varying)::text, ('mail'::character varying)::text]))));
ALTER TABLE "stewardship_service_status" ADD CONSTRAINT "service_status_sender" CHECK (((((sender_since IS NOT NULL) AND ((sender_state)::text = ANY (ARRAY[('running'::character varying)::text, ('outage_paused'::character varying)::text, ('gmail_held'::character varying)::text, ('daily_limit'::character varying)::text, ('halted'::character varying)::text])) AND ((service)::text = 'mail-dispatch'::text)) OR ((NOT ((service)::text = 'mail-dispatch'::text)) AND (sender_since IS NULL) AND (sender_state IS NULL))) AND ((sender_until IS NULL) OR ((sender_state)::text = ANY (ARRAY[('outage_paused'::character varying)::text, ('gmail_held'::character varying)::text])))));
CREATE INDEX "service_status_reported" ON "stewardship_service_status" ("reported_at");

CREATE TABLE "stewardship_source_drop_count" ("id" uuid NOT NULL PRIMARY KEY, "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "actor_id" uuid NULL, "correlation_id" uuid NOT NULL, "measure" varchar(32) NOT NULL, "before" bigint NULL CHECK ("before" >= 0), "after" bigint NOT NULL CHECK ("after" >= 0), "limit_percent" smallint NOT NULL CHECK ("limit_percent" >= 0), "failed" boolean NOT NULL, "attempt_id" uuid NOT NULL, CONSTRAINT "source_drop_count_identity" UNIQUE ("attempt_id", "measure"), CONSTRAINT "source_drop_count_limit" CHECK ("limit_percent" <= 100), CONSTRAINT "source_drop_count_failure" CHECK ((NOT "failed" OR ("before" > 0 AND "limit_percent" < 100))));
ALTER TABLE "stewardship_source_drop_count" ADD CONSTRAINT "source_drop_count_measure" CHECK (((measure)::text = ANY (ARRAY[('family'::character varying)::text, ('member'::character varying)::text, ('ministry'::character varying)::text, ('roster'::character varying)::text, ('fund'::character varying)::text, ('portal_eligible_families'::character varying)::text, ('email_eligible_families'::character varying)::text, ('active_head_families'::character varying)::text, ('valid_email_contacts'::character varying)::text])));
ALTER TABLE "stewardship_source_drop_count" ADD CONSTRAINT "stewardship_source_d_attempt_id_d243a330_fk_stewardsh" FOREIGN KEY ("attempt_id") REFERENCES "stewardship_source_refresh_attempt" ("id") DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX "stewardship_source_drop_count_correlation_id_2d4abf07" ON "stewardship_source_drop_count" ("correlation_id");
CREATE INDEX "stewardship_source_drop_count_attempt_id_d243a330" ON "stewardship_source_drop_count" ("attempt_id");

-- Each online service's login writes only its own service's rows: web,
-- worker, scheduler, mail dispatch and the configuration installer by their
-- service name, a credential installer by its target. Identity, start time
-- and version never change; reported_at is always the database clock, as is
-- started_at on insert, and sender_since is when the database first saw the
-- current sender state, so no host clock can make a process look alive or a
-- state look older. Only the worker deletes, and only a row not reported for
-- a day (its hourly housekeeping). The schema owner is exempt from the login
-- and deletion checks and may supply its own times, as in the other guards,
-- so migrations and the disposable test schema can write directly.
-- Invoker's rights: it reads nothing but the catalog. There is no cap on
-- rows per login: a restart adds a row only after the process passed its
-- startup admission, rows hold a few fixed-size columns, and housekeeping
-- removes them a day after their last report, so even a process restarting
-- every minute adds about 1,440 small rows that the page groups by service.
CREATE FUNCTION public.stewardship_service_status_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE
    owner boolean := pg_has_role(current_user,
        (SELECT nspowner FROM pg_namespace WHERE nspname='public'),'USAGE');
    writer text;
BEGIN
    IF TG_OP='DELETE' THEN
        IF owner OR (current_user='pk_stewardship_worker'
                     AND OLD.reported_at<statement_timestamp()-interval '1 day') THEN
            RETURN OLD;
        END IF;
        RAISE EXCEPTION 'Only housekeeping removes a service status record, a day after its last report' USING ERRCODE='42501';
    END IF;
    writer := CASE WHEN NEW.service='credential-installer'
                   THEN 'pk_stewardship_credential_'||NEW.target
                   ELSE 'pk_stewardship_'||replace(NEW.service,'-','_') END;
    IF NOT owner AND current_user IS DISTINCT FROM writer THEN
        RAISE EXCEPTION 'A service reports only its own status' USING ERRCODE='42501';
    END IF;
    IF TG_OP='UPDATE' AND (NEW.id IS DISTINCT FROM OLD.id
        OR NEW.service IS DISTINCT FROM OLD.service
        OR NEW.process IS DISTINCT FROM OLD.process
        OR NEW.target IS DISTINCT FROM OLD.target
        OR NEW.started_at IS DISTINCT FROM OLD.started_at
        OR NEW.application_version IS DISTINCT FROM OLD.application_version) THEN
        RAISE EXCEPTION 'A service status record keeps its identity' USING ERRCODE='23514';
    END IF;
    IF owner THEN
        RETURN NEW;
    END IF;
    IF TG_OP='INSERT' THEN
        NEW.started_at:=statement_timestamp();
    END IF;
    NEW.reported_at:=statement_timestamp();
    NEW.sender_since:=CASE
        WHEN NEW.sender_state IS NULL THEN NULL
        WHEN TG_OP='UPDATE' AND NEW.sender_state=OLD.sender_state THEN OLD.sender_since
        ELSE statement_timestamp() END;
    RETURN NEW;
END $$;

-- Drop counts are append-only.
CREATE FUNCTION public.stewardship_source_drop_count_immutable_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    RAISE EXCEPTION 'Historical records are append-only' USING ERRCODE='23514';
END $$;

-- Only the worker records drop counts, for an attempt it has already
-- rejected (the refusal's own transaction), and "failed" must be exactly the
-- loader's rule (source/loading.py, _fell): a nonzero baseline under a limit
-- below 100 percent that fell to zero or by more than the limit.
CREATE FUNCTION public.stewardship_source_drop_count_insert_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF current_user<>'pk_stewardship_worker'
       AND NOT pg_has_role(current_user,
           (SELECT nspowner FROM pg_namespace WHERE nspname='public'),'USAGE') THEN
        RAISE EXCEPTION 'Only the worker records a refused load''s counts' USING ERRCODE='42501';
    END IF;
    IF NEW.created_at>statement_timestamp() THEN
        RAISE EXCEPTION 'Drop counts cannot be dated in the future' USING ERRCODE='23514';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM public.stewardship_source_refresh_attempt a
                   JOIN public.stewardship_source_snapshot s ON s.id=a.snapshot_id
                   WHERE a.id=NEW.attempt_id AND s.state='rejected') THEN
        RAISE EXCEPTION 'Drop counts belong to a rejected refresh attempt' USING ERRCODE='23514';
    END IF;
    IF NEW.failed IS DISTINCT FROM coalesce(NEW.limit_percent<100 AND NEW.before>0
        AND (NEW.after=0 OR (NEW.before-NEW.after)*100>NEW.before*NEW.limit_percent),false) THEN
        RAISE EXCEPTION 'A drop count''s failure must follow the loss rule' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER stewardship_service_status_guard BEFORE INSERT OR UPDATE OR DELETE ON public.stewardship_service_status
FOR EACH ROW EXECUTE FUNCTION public.stewardship_service_status_guard_v1();
CREATE TRIGGER stewardship_source_drop_count_immutable_guard_v1 BEFORE UPDATE OR DELETE ON public.stewardship_source_drop_count
FOR EACH ROW EXECUTE FUNCTION public.stewardship_source_drop_count_immutable_v1();
CREATE TRIGGER stewardship_source_drop_count_insert_guard BEFORE INSERT ON public.stewardship_source_drop_count
FOR EACH ROW EXECUTE FUNCTION public.stewardship_source_drop_count_insert_v1();
REVOKE ALL ON FUNCTION public.stewardship_service_status_guard_v1() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.stewardship_source_drop_count_immutable_v1() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.stewardship_source_drop_count_insert_v1() FROM PUBLIC;

-- A service status write that fails (other than at its own time limits)
-- keeps a durable service_status_failed event. The full list, copied from
-- the fresh-install tables.sql as it stands at this release; ADD CONSTRAINT
-- validates the existing rows.
ALTER TABLE public.stewardship_operational_log DROP CONSTRAINT operational_event_safe;
ALTER TABLE public.stewardship_operational_log ADD CONSTRAINT operational_event_safe CHECK (event IN (
        'configuration_rejected','configuration_digest_mismatch','startup_rejected',
        'startup_validated','request_completed','report_audit_failed','report_shaping_failed',
        'task_started','task_completed',
        'task_failed','fact_drift','unstructured_log_suppressed','authentication_limits_weakened',
        'installer_request_failed','source_refresh_invalid','source_member_unusable',
        'source_ministry_name_repaired','source_retention_skipped',
        'source_tenant_mismatch','source_destructive_change',
        'source_refresh_held','source_credential_failed','source_provider_failed',
        'mail_provider_failed',
        'due_work_lag',
        'credential_handoff_key_mismatch','setup_credential_staged','delivery_unknown',
        'setup_credential_scrubbed','campaign_boundary_lag','production_cleanup_failed',
        'authentication_health_observation_failed',
        'task_timed_out','helper_timed_out','work_budget_reached','task_lease_lost',
        'family_engagement_failed','service_status_failed'));

-- Refuse to commit unless everything above is installed, so an upgrade
-- cannot report success with part of it missing.
DO $check$
BEGIN
    IF to_regclass('public.stewardship_service_status') IS NULL
       OR to_regclass('public.stewardship_source_drop_count') IS NULL THEN
        RAISE EXCEPTION 'The System health tables were not created';
    END IF;
    IF (SELECT count(*) FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid
        WHERE c.relname IN ('service_status_reported',
                            'stewardship_source_drop_count_correlation_id_2d4abf07',
                            'stewardship_source_drop_count_attempt_id_d243a330')
          AND i.indrelid IN ('public.stewardship_service_status'::regclass,
                             'public.stewardship_source_drop_count'::regclass))<>3 THEN
        RAISE EXCEPTION 'The System health indexes were not created';
    END IF;
    IF (SELECT count(*) FROM pg_constraint
        WHERE conrelid IN ('public.stewardship_service_status'::regclass,
                           'public.stewardship_source_drop_count'::regclass)
          AND conname IN ('service_status_names','service_status_process',
                          'service_status_target','service_status_version',
                          'service_status_times','service_status_sender',
                          'source_drop_count_identity','source_drop_count_measure',
                          'source_drop_count_limit','source_drop_count_failure'))<>10
       OR NOT EXISTS (SELECT 1 FROM pg_constraint
            WHERE conrelid='public.stewardship_source_drop_count'::regclass
              AND conname='stewardship_source_d_attempt_id_d243a330_fk_stewardsh'
              AND contype='f' AND condeferrable AND condeferred
              AND confrelid='public.stewardship_source_refresh_attempt'::regclass) THEN
        RAISE EXCEPTION 'The System health constraints were not installed';
    END IF;
    IF position('service_status_failed' IN pg_get_constraintdef(
           (SELECT oid FROM pg_constraint
            WHERE conrelid='public.stewardship_operational_log'::regclass
              AND conname='operational_event_safe')))=0
       OR position('family_engagement_failed' IN pg_get_constraintdef(
           (SELECT oid FROM pg_constraint
            WHERE conrelid='public.stewardship_operational_log'::regclass
              AND conname='operational_event_safe')))=0 THEN
        RAISE EXCEPTION 'operational_event_safe does not list service_status_failed';
    END IF;
    -- Each guard on its own table, enabled, BEFORE and FOR EACH ROW, with
    -- its events: tgtype 31 is INSERT, UPDATE and DELETE; 27 UPDATE and
    -- DELETE; 7 INSERT only.
    IF (SELECT count(*) FROM pg_trigger t
        WHERE NOT t.tgisinternal AND t.tgenabled='O' AND (t.tgrelid, t.tgname, t.tgtype, t.tgfoid) IN (
            ('public.stewardship_service_status'::regclass,
             'stewardship_service_status_guard', 31,
             'public.stewardship_service_status_guard_v1()'::regprocedure),
            ('public.stewardship_source_drop_count'::regclass,
             'stewardship_source_drop_count_immutable_guard_v1', 27,
             'public.stewardship_source_drop_count_immutable_v1()'::regprocedure),
            ('public.stewardship_source_drop_count'::regclass,
             'stewardship_source_drop_count_insert_guard', 7,
             'public.stewardship_source_drop_count_insert_v1()'::regprocedure)))<>3 THEN
        RAISE EXCEPTION 'The System health guards were not installed';
    END IF;
    -- New functions only (none replaced): each is invoker's rights with the
    -- fixed search_path, none SECURITY DEFINER, and PUBLIC may not call it.
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public'
          AND p.proname IN ('stewardship_service_status_guard_v1',
                            'stewardship_source_drop_count_immutable_v1',
                            'stewardship_source_drop_count_insert_v1')
          AND NOT p.prosecdef
          AND p.proconfig @> ARRAY['search_path=pg_catalog, public, pg_temp']
          AND NOT EXISTS (SELECT 1 FROM aclexplode(coalesce(p.proacl,
                  acldefault('f', p.proowner))) a
              WHERE a.grantee=0 AND a.privilege_type='EXECUTE'))<>3 THEN
        RAISE EXCEPTION 'A System health function has the wrong security, search_path or grants';
    END IF;
END
$check$;
