-- Frozen forward migration file 0078 (the repository-wide file sequence):
-- the debug-off switch, server side (ADM-13 PR 6a, #530). This file is
-- installed by its Django migration in the jobs app and must never change
-- once released; tests/stewardship/test_schema_migration_files.py pins its
-- digest. A later change gets its own numbered file. A fresh install runs
-- the baseline, the earlier forward files and then this; the baseline
-- tables.sql already carries the widened event list, so the install ends in
-- the same catalog as an upgraded database. Reversing needs its own forward
-- migration.
--
-- Debug logging (PARISHKIT_DEBUG_LOGGING=1 in a service's environment) keeps
-- free text and error details that can hold personal data, and until now
-- only recreating the services turned it off. A set switch (a row whose
-- cleared_at is empty) makes every process that reaches the database turn
-- debug logging off for itself (parishkit.stewardship.debug_switch) until the
-- switch is cleared. It can never turn debug logging on in a process started
-- without the setting.
--
-- stewardship_debug_off_switch: who set the switch, when, the session and its
-- fresh sign-in instant, and when and by whom it was cleared. No path, host,
-- address or credential.
--
-- The guard is the boundary:
--   INSERT: only the web login (the schema owner is not checked), for an
--     enabled Administrator whose live session signed in within the last
--     five minutes or is a live full-scope automation session's command
--     login (the check stewardship_backup_request_guard_v1 makes), and only
--     while no switch is set. Inserts are serialized by a transaction
--     advisory lock so two cannot both see none. The row starts set, with
--     every time from the database clock. (The web's INSERT grant comes with
--     System health's Turn off debug logging, ADM-13 PR 6b.)
--   UPDATE: only clears, and only once. Identity, actor, session, sign-in and
--     set time never change, and the clear time is the database's. Until PR
--     6b admits an Administrator's clear in Testing mode, only the schema
--     owner clears, which the operator's definer below runs as.
--   DELETE: never.
--   Non-owner changes run only under READ COMMITTED, so every check reads
--   the rows as committed when the statement ran.
-- Invoker's rights with a fixed search_path.
--
-- stewardship_debug_logging_off_v1() answers one boolean, whether a switch
-- is set, so no process login reads who set it. SECURITY DEFINER, STABLE,
-- with EXECUTE for each runtime login (runtime_grants.runtime_functions);
-- PUBLIC has none. The backup login holds no definer grant (its admission
-- refuses any) and reads the table through pg_read_all_data instead.
--
-- stewardship_debug_off_clear_v1() clears a set switch as the server
-- operator (cleared_by 'operator') and returns how many rows it cleared, 0
-- or 1. SECURITY DEFINER, with EXECUTE for the offline admin-recovery login
-- only (pk-stewardship debug-off-clear), which SQL can tell apart from the
-- web login that pk-admin and the online commands share; PUBLIC has none.
--
-- operational_event_safe gains debug_logging_switched_off, the WARNING
-- process-log line a process writes when it applies the switch. Nothing
-- writes it to the database, so no writer allow-list changes; the check keeps
-- every Python event writable, as a database test requires.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

CREATE TABLE "stewardship_debug_off_switch" ("id" uuid NOT NULL PRIMARY KEY, "set_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "actor_id" uuid NOT NULL, "session_id" uuid NOT NULL, "authenticated_at" timestamp with time zone NOT NULL, "cleared_at" timestamp with time zone NULL, "cleared_by" varchar(16) NULL, "cleared_actor_id" uuid NULL, CONSTRAINT "debug_off_switch_cleared" CHECK ((("cleared_actor_id" IS NULL AND "cleared_at" IS NULL AND "cleared_by" IS NULL) OR ("cleared_actor_id" IS NULL AND "cleared_at" IS NOT NULL AND "cleared_by" = 'operator') OR ("cleared_actor_id" IS NOT NULL AND "cleared_at" IS NOT NULL AND "cleared_by" = 'administrator'))));
CREATE INDEX "debug_off_switch_newest" ON "stewardship_debug_off_switch" ("set_at" DESC);

CREATE FUNCTION public.stewardship_debug_off_switch_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE
    owner boolean := pg_has_role(current_user,
        (SELECT nspowner FROM pg_namespace WHERE nspname='public'),'USAGE');
    instant timestamptz := statement_timestamp();
BEGIN
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'The debug-off switch is never deleted' USING ERRCODE='42501';
    END IF;
    -- The insert's check reads other rows at the statement's own snapshot,
    -- which only READ COMMITTED gives a statement that waited on the lock.
    IF NOT owner AND current_setting('transaction_isolation')<>'read committed' THEN
        RAISE EXCEPTION 'A debug-off switch change requires READ COMMITTED' USING ERRCODE='42501';
    END IF;
    IF TG_OP='INSERT' THEN
        IF owner THEN
            RETURN NEW;
        END IF;
        IF current_user IS DISTINCT FROM 'pk_stewardship_web' THEN
            RAISE EXCEPTION 'Only the web portal sets the debug-off switch' USING ERRCODE='42501';
        END IF;
        -- One set switch at a time: serialize inserts before looking.
        PERFORM pg_advisory_xact_lock(736241, 3);
        IF NEW.actor_id IS NULL
           OR public.stewardship_export_authorized_v1(NEW.actor_id,true) IS NOT TRUE
           OR NOT EXISTS(SELECT 1 FROM public.stewardship_portal_session login
                WHERE login.id=NEW.session_id AND login.principal_id=NEW.actor_id
                  AND login.revoked_at IS NULL AND login.expires_at>clock_timestamp()
                  AND login.last_activity_at>clock_timestamp()-interval '60 minutes'
                  AND login.authenticated_at=NEW.authenticated_at
                  AND (login.authenticated_at BETWEEN clock_timestamp()-interval '5 minutes'
                           AND clock_timestamp()
                       -- A live full-scope automation session stands in for a
                       -- fresh sign-in, as in the other fresh-gated guards.
                       OR public.stewardship_automation_fresh_v1(login.id,NEW.actor_id))) THEN
            RAISE EXCEPTION 'The debug-off switch requires fresh Administrator authentication' USING ERRCODE='42501';
        END IF;
        IF EXISTS(SELECT 1 FROM public.stewardship_debug_off_switch s
                  WHERE s.cleared_at IS NULL) THEN
            RAISE EXCEPTION 'The debug-off switch is already set' USING ERRCODE='23505';
        END IF;
        NEW.set_at:=instant;
        NEW.cleared_at:=NULL;
        NEW.cleared_by:=NULL;
        NEW.cleared_actor_id:=NULL;
        RETURN NEW;
    END IF;
    IF NOT owner THEN
        RAISE EXCEPTION 'Only the server operator clears the debug-off switch' USING ERRCODE='42501';
    END IF;
    IF NEW.id IS DISTINCT FROM OLD.id OR NEW.set_at IS DISTINCT FROM OLD.set_at
       OR NEW.actor_id IS DISTINCT FROM OLD.actor_id
       OR NEW.session_id IS DISTINCT FROM OLD.session_id
       OR NEW.authenticated_at IS DISTINCT FROM OLD.authenticated_at THEN
        RAISE EXCEPTION 'A debug-off switch keeps who set it and when' USING ERRCODE='23514';
    END IF;
    IF OLD.cleared_at IS NOT NULL OR NEW.cleared_at IS NULL THEN
        RAISE EXCEPTION 'A debug-off switch is cleared once and stays cleared' USING ERRCODE='23514';
    END IF;
    NEW.cleared_at:=instant;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.stewardship_debug_off_switch_guard_v1() FROM PUBLIC;
CREATE TRIGGER stewardship_debug_off_switch_guard BEFORE INSERT OR UPDATE OR DELETE ON public.stewardship_debug_off_switch
FOR EACH ROW EXECUTE FUNCTION public.stewardship_debug_off_switch_guard_v1();

CREATE FUNCTION public.stewardship_debug_logging_off_v1() RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT EXISTS(SELECT 1 FROM public.stewardship_debug_off_switch WHERE cleared_at IS NULL)
$$;
ALTER FUNCTION public.stewardship_debug_logging_off_v1() SECURITY DEFINER;
REVOKE ALL ON FUNCTION public.stewardship_debug_logging_off_v1() FROM PUBLIC;

CREATE FUNCTION public.stewardship_debug_off_clear_v1() RETURNS integer
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE
    cleared integer;
BEGIN
    -- The guard sets the clear time; this names who cleared it.
    UPDATE public.stewardship_debug_off_switch
       SET cleared_at=statement_timestamp(), cleared_by='operator', cleared_actor_id=NULL
     WHERE cleared_at IS NULL;
    GET DIAGNOSTICS cleared = ROW_COUNT;
    RETURN cleared;
END $$;
ALTER FUNCTION public.stewardship_debug_off_clear_v1() SECURITY DEFINER;
REVOKE ALL ON FUNCTION public.stewardship_debug_off_clear_v1() FROM PUBLIC;

-- The full event list: 0038's plus the new name. ADD CONSTRAINT validates
-- the existing rows, which the old list already admitted.
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
        'family_engagement_failed','service_status_failed','incident_recovered',
        'startup_waiting','startup_wait_ended','debug_logging_enabled',
        'refresh_lead_window_conflict','web_unhealthy',
        'go_live_refresh_held','go_live_step_refused','go_live_sequencing_stopped',
        'admin_command_failed','debug_logging_switched_off'));

-- Refuse to commit unless the table (with no PUBLIC privilege), its check
-- and index, the guard (invoker's rights, fixed search_path, no PUBLIC
-- EXECUTE) and its trigger, both definers (fixed search_path, no PUBLIC EXECUTE) and the widened event
-- check are all installed as declared. Missing objects are checked first, on
-- their own, because PostgreSQL does not short-circuit OR and the
-- ::regprocedure casts below would otherwise fail with their own error.
DO $check$
BEGIN
    IF to_regclass('public.stewardship_debug_off_switch') IS NULL
       OR to_regprocedure('public.stewardship_debug_off_switch_guard_v1()') IS NULL
       OR to_regprocedure('public.stewardship_debug_logging_off_v1()') IS NULL
       OR to_regprocedure('public.stewardship_debug_off_clear_v1()') IS NULL THEN
        RAISE EXCEPTION 'Migration 0078 (debug-off switch) is not installed as declared';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM pg_constraint
        WHERE conrelid='public.stewardship_debug_off_switch'::regclass
          AND conname='debug_off_switch_cleared' AND contype='c' AND convalidated) THEN
        RAISE EXCEPTION 'The debug-off switch check was not created';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid
        WHERE c.relname='debug_off_switch_newest'
          AND i.indrelid='public.stewardship_debug_off_switch'::regclass
          AND i.indisvalid) THEN
        RAISE EXCEPTION 'The debug-off switch index was not created';
    END IF;
    -- PUBLIC holds no privilege on the table (grantee 0 in its ACL).
    IF EXISTS(SELECT 1 FROM pg_class c, aclexplode(coalesce(c.relacl,
                  acldefault('r',c.relowner))) a
              WHERE c.oid='public.stewardship_debug_off_switch'::regclass
                AND a.grantee=0) THEN
        RAISE EXCEPTION 'PUBLIC holds a privilege on the debug-off switch';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM pg_proc p
        WHERE p.oid='public.stewardship_debug_off_switch_guard_v1()'::regprocedure
          AND NOT p.prosecdef
          AND p.proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
          AND p.prosrc LIKE '%transaction_isolation%'
          AND p.prosrc LIKE '%pg_advisory_xact_lock(736241, 3)%'
          AND p.prosrc LIKE '%public.stewardship_automation_fresh_v1(login.id,NEW.actor_id)%'
          AND p.prosrc LIKE '%Only the server operator clears%')
       OR has_function_privilege('public',
            'public.stewardship_debug_off_switch_guard_v1()', 'EXECUTE') THEN
        RAISE EXCEPTION 'The debug-off switch guard is not the one this file installs';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM pg_trigger t
        WHERE t.tgrelid='public.stewardship_debug_off_switch'::regclass
          AND t.tgname='stewardship_debug_off_switch_guard' AND t.tgenabled='O'
          AND t.tgfoid='public.stewardship_debug_off_switch_guard_v1()'::regprocedure
          AND t.tgtype=31) THEN
        RAISE EXCEPTION 'The debug-off switch guard trigger was not created';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM pg_proc p
        WHERE p.oid='public.stewardship_debug_logging_off_v1()'::regprocedure
          AND p.prosecdef AND p.provolatile='s' AND NOT p.proretset
          AND p.prorettype='boolean'::regtype
          AND p.proconfig=ARRAY['search_path=pg_catalog, public, pg_temp'])
       OR has_function_privilege('public',
            'public.stewardship_debug_logging_off_v1()', 'EXECUTE') THEN
        RAISE EXCEPTION 'The debug-off switch read is not installed as declared';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM pg_proc p
        WHERE p.oid='public.stewardship_debug_off_clear_v1()'::regprocedure
          AND p.prosecdef AND p.provolatile='v' AND NOT p.proretset
          AND p.prorettype='integer'::regtype
          AND p.proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
          AND p.prosrc LIKE '%cleared_by=''operator''%')
       OR has_function_privilege('public',
            'public.stewardship_debug_off_clear_v1()', 'EXECUTE') THEN
        RAISE EXCEPTION 'The operator''s debug-off clear is not installed as declared';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM pg_constraint
        WHERE conrelid='public.stewardship_operational_log'::regclass
          AND conname='operational_event_safe' AND contype='c' AND convalidated
          AND pg_get_constraintdef(oid) LIKE '%debug_logging_switched_off%'
          AND pg_get_constraintdef(oid) LIKE '%admin_command_failed%') THEN
        RAISE EXCEPTION 'The operational event check does not admit debug_logging_switched_off';
    END IF;
END
$check$;
