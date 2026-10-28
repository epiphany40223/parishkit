-- Frozen forward migration file 0193 (the repository-wide file sequence):
-- the schema half of the dedicated off-site backup uploader's preparation
-- release (#235; operations spec, "Dedicated off-site uploader"). This file
-- is installed by its Django migration in the jobs app and must never
-- change once released; tests/stewardship/test_schema_migration_files.py
-- pins its digest and checks its copy of the replaced baseline function
-- against the baseline. Reversing needs its own forward migration.
--
-- Nothing here does anything yet. The uploader's login,
-- pk_stewardship_backup_uploader, is created by a later release (with its
-- grants and its connection source rule); this file only names it, and a
-- function body may name a role that does not exist: the names below are
-- compared with session_user and current_user as text, so until that login
-- exists no session matches them. No GRANT names the login here (a GRANT to
-- a missing role is an error, and grants come from the runtime registry).
--
--   stewardship_backup_destination_v1   0176's definer, replaced in place
--     with the same signature and output columns (so CREATE OR REPLACE is
--     allowed) and one change: its caller check admits the uploader's
--     session login as well as the Google Workspace installer's. EXECUTE
--     stays with the installer alone until the registry grants the
--     uploader; SECURITY DEFINER is stated again and PUBLIC is revoked
--     again, as for every replaced definer.
--   stewardship_operational_log_writer_v1   the writer trigger, replaced
--     with its baseline body and one change: the uploader joins the logins
--     that may record the four timeout events. It is an invoker and must
--     stay one, so it gets no ALTER (schema/functions.sql holds the same
--     final definition).
--
-- The DO block at the end refuses to commit unless both are installed as
-- declared: the destination still a stable definer with the pinned
-- search_path, both callers and no PUBLIC execute; the writer still not a
-- definer, behind its trigger, naming the uploader once, in the timeout
-- clause. No temporary objects: the migration login has no TEMP privilege.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

CREATE OR REPLACE FUNCTION public.stewardship_backup_destination_v1(OUT backup_target text, OUT delegated_email text)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    -- Granted to the Google Workspace installer (#269) and, from the
    -- release that creates its login, the off-site uploader (#235); refuse
    -- any other session login too, unless it is a member of the schema
    -- owner (such as the migration login).
    IF session_user<>'pk_stewardship_credential_google_workspace'
       AND session_user<>'pk_stewardship_backup_uploader' AND NOT pg_has_role(session_user,
        (SELECT nspowner FROM pg_namespace WHERE nspname='public'),'USAGE') THEN
        RAISE EXCEPTION 'Only the Google Workspace installer and the backup uploader read the backup destination'
            USING ERRCODE='42501';
    END IF;
    SELECT backup.settings->>'target', workspace.settings->>'delegated_email'
    INTO backup_target, delegated_email
    FROM public.stewardship_system_configuration runtime
    LEFT JOIN public.stewardship_applied_integration backup
        ON backup.configuration_id=runtime.active_configuration_id
        AND backup.kind='backup'
    LEFT JOIN public.stewardship_applied_integration workspace
        ON workspace.configuration_id=runtime.active_configuration_id
        AND workspace.kind='google_workspace';
END $$;
-- Stated again on its own, as every definer is.
ALTER FUNCTION public.stewardship_backup_destination_v1() SECURITY DEFINER;
REVOKE ALL ON FUNCTION public.stewardship_backup_destination_v1() FROM PUBLIC;

-- Replaces the baseline's body; only the uploader in the timeout clause
-- (and its comments) is new. An invoker, so no ALTER follows.
CREATE OR REPLACE FUNCTION public.stewardship_operational_log_writer_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    -- Each runtime login that writes directly may record only the entries
    -- its own code writes (#293 for mail dispatch and backup, #389 L2 for
    -- web, worker and scheduler, #235 for the off-site uploader): a closed
    -- (schema, event, level) list, no actor to impersonate, and the
    -- database's own time. The one exception
    -- is web's refused Admin input (#1403), which must name the signed-in
    -- user it refused, so no anonymous refusal is kept; every other entry
    -- names none. That actor is a bare id web supplies and is taken on
    -- trust, as on every audit record web writes: a compromised web login
    -- could name another user here. Web never writes CRITICAL, which pages
    -- and opens incidents. The schema owner, and so every SECURITY DEFINER
    -- writer, is not limited here; no other login holds INSERT.
    IF pg_has_role(current_user,
        (SELECT nspowner FROM pg_namespace WHERE nspname='public'),'USAGE') THEN
        RETURN NEW;
    END IF;
    IF (NEW.actor_id IS NOT NULL)<>(NEW.event='admin_input_refused') OR NOT (
        -- Work stopped by a time limit, from any runtime login. The
        -- off-site uploader's login (#235) is created by the release that
        -- adds the uploader; until then no session has its name.
        (current_user IN ('pk_stewardship_web','pk_stewardship_worker',
                'pk_stewardship_scheduler','pk_stewardship_mail_dispatch',
                'pk_stewardship_backup_worker','pk_stewardship_backup_uploader')
         AND NEW.schema='timeout'
         AND NEW.event IN ('task_timed_out','helper_timed_out',
                'work_budget_reached','task_lease_lost')
         AND NEW.level IN ('INFO','WARNING','ERROR'))
        -- Web: an unusable source Member on a Family form, a Family
        -- engagement record that could not be written, an Admin
        -- command-line command that failed unexpectedly (#617) and a
        -- refused Admin input (#1403).
        OR (current_user='pk_stewardship_web' AND (
            (NEW.schema,NEW.event,NEW.level) IN (
                ('member_source','source_member_unusable','WARNING'),
                ('failure','family_engagement_failed','ERROR'),
                ('failure','admin_command_failed','ERROR'),
                ('refusal','admin_input_refused','INFO'),
                ('refusal','admin_input_refused','WARNING'))))
        -- Worker: source refresh, setup load and finalize failures; source
        -- retention; operational collection; fact verification and export
        -- cleanup failures.
        OR (current_user='pk_stewardship_worker' AND (
            (NEW.schema='failure'
             AND NEW.event IN ('source_tenant_mismatch','source_destructive_change',
                    'source_provider_failed','source_refresh_invalid',
                    'source_refresh_held','source_credential_failed')
             AND NEW.level IN ('INFO','WARNING','CRITICAL'))
            OR (NEW.schema,NEW.event,NEW.level) IN (
                ('failure','source_retention_skipped','ERROR'),
                ('failure','task_failed','ERROR'),
                ('failure','task_failed','CRITICAL'),
                ('member_source','source_member_unusable','WARNING'),
                ('exception','configuration_digest_mismatch','WARNING'),
                ('task','fact_drift','CRITICAL'))))
        -- Scheduler: a held slot production, boundary lag, due-work lag
        -- (whose health trigger runs as the scheduler) and the web health
        -- alert (#787's trigger, which also runs as the scheduler).
        OR (current_user='pk_stewardship_scheduler' AND (
            (NEW.schema,NEW.event,NEW.level) IN (
                ('schedule','source_refresh_held','INFO'),
                ('due_work','campaign_boundary_lag','WARNING'),
                ('due_work','due_work_lag','CRITICAL'),
                ('failure','web_unhealthy','CRITICAL'))))
        -- Mail dispatch: an address follow-up's permanent refusal that
        -- could not be set aside (#1201), CRITICAL so an Administrator sees
        -- it.
        OR (current_user='pk_stewardship_mail_dispatch' AND
            (NEW.schema,NEW.event,NEW.level) IN (
                ('failure','retry_refusal_unrecorded','CRITICAL')))) THEN
        RAISE EXCEPTION 'This login may not record this operational entry'
            USING ERRCODE = '42501';
    END IF;
    NEW.created_at := statement_timestamp();
    RETURN NEW;
END;
$$;

-- Refuse to commit unless both are installed as declared. It reads only the
-- catalog. A missing function is checked first, on its own, because
-- PostgreSQL does not short-circuit OR and the ::regprocedure casts below
-- would otherwise fail with their own error.
DO $check$
BEGIN
    IF to_regprocedure('public.stewardship_backup_destination_v1()') IS NULL
       OR to_regprocedure('public.stewardship_operational_log_writer_v1()') IS NULL THEN
        RAISE EXCEPTION 'Migration 0193 (backup uploader preparation) is not installed';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_proc p
           WHERE p.oid='public.stewardship_backup_destination_v1()'::regprocedure
             AND p.prosecdef AND p.provolatile='s' AND NOT p.proretset
             AND p.prorettype='record'::regtype
             AND p.proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
             AND p.prosrc LIKE '%session_user<>''pk_stewardship_credential_google_workspace''%'
             AND p.prosrc LIKE '%session_user<>''pk_stewardship_backup_uploader'' AND NOT pg_has_role(session_user,%'
             AND p.prosrc LIKE '%backup.kind=''backup''%'
             AND p.prosrc LIKE '%workspace.kind=''google_workspace''%')
       OR has_function_privilege('public',
           'public.stewardship_backup_destination_v1()', 'EXECUTE') THEN
        RAISE EXCEPTION 'Migration 0193: the backup destination is not installed as declared';
    END IF;
    -- The writer: the same enabled BEFORE INSERT row trigger, still an
    -- invoker, naming the uploader exactly once, in the timeout clause.
    IF NOT EXISTS (SELECT 1 FROM pg_trigger t JOIN pg_proc p ON p.oid=t.tgfoid
           WHERE t.tgname='stewardship_operational_log_writer_v1'
             AND t.tgrelid='public.stewardship_operational_log'::regclass
             AND t.tgenabled='O' AND t.tgtype=7
             AND p.oid='public.stewardship_operational_log_writer_v1()'::regprocedure
             AND NOT p.prosecdef
             AND p.prosrc ~ '''pk_stewardship_backup_worker'',''pk_stewardship_backup_uploader''\)\s+AND NEW\.schema=''timeout'''
             AND array_length(regexp_split_to_array(p.prosrc, 'pk_stewardship_backup_uploader'), 1)=2
             AND position('(NEW.actor_id IS NOT NULL)<>(NEW.event=''admin_input_refused'')' IN p.prosrc)>0
             AND position('(''failure'',''retry_refusal_unrecorded'',''CRITICAL'')' IN p.prosrc)>0
             AND position('This login may not record this operational entry' IN p.prosrc)>0) THEN
        RAISE EXCEPTION 'Migration 0193: the operational log writer is not installed as declared';
    END IF;
END
$check$;
