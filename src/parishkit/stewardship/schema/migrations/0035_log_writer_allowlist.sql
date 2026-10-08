-- Frozen forward migration file 0035 (the repository-wide file sequence):
-- every runtime login that writes the operational log directly gets a closed
-- list of the entries its own code writes (#389 L2). This file is installed
-- by its Django migration and must never change once released;
-- tests/stewardship/test_schema_migration_files.py pins its digest. A later
-- change gets its own numbered file. A fresh install runs the baseline, the
-- earlier files and then this, and ends in the same catalog as an upgraded
-- database.
--
-- The writer trigger limited only mail dispatch and the backup login, to
-- timeout entries (#293). Web, worker and scheduler could write any level
-- and event, so a compromised web could, for example, write CRITICAL entries
-- that page the Administrator and open incidents. Each now has an explicit
-- (schema, event, level) list, taken from every write path in its code; all
-- direct writers keep a NULL actor and the database's own time. The schema
-- owner, and so every SECURITY DEFINER writer, stays unrestricted.
--
-- CREATE OR REPLACE keeps the function's identity, attributes and trigger;
-- the body equals the fresh-install copy in schema/functions.sql, whose md5
-- database-grants and the upgrade check compare with the installed body.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

CREATE OR REPLACE FUNCTION public.stewardship_operational_log_writer_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    -- Each runtime login that writes directly may record only the entries
    -- its own code writes (#293 for mail dispatch and backup, #389 L2 for
    -- web, worker and scheduler): a closed (schema, event, level) list, no
    -- actor to impersonate, and the database's own time. Web never writes
    -- CRITICAL, which pages and opens incidents. The schema owner, and so
    -- every SECURITY DEFINER writer, is not limited here; no other login
    -- holds INSERT.
    IF pg_has_role(current_user,
        (SELECT nspowner FROM pg_namespace WHERE nspname='public'),'USAGE') THEN
        RETURN NEW;
    END IF;
    IF NEW.actor_id IS NOT NULL OR NOT (
        -- Work stopped by a time limit, from any runtime login.
        (current_user IN ('pk_stewardship_web','pk_stewardship_worker',
                'pk_stewardship_scheduler','pk_stewardship_mail_dispatch',
                'pk_stewardship_backup_worker')
         AND NEW.schema='timeout'
         AND NEW.event IN ('task_timed_out','helper_timed_out',
                'work_budget_reached','task_lease_lost')
         AND NEW.level IN ('INFO','WARNING','ERROR'))
        -- Web: an unusable source Member on a Family form, and a Family
        -- engagement record that could not be written.
        OR (current_user='pk_stewardship_web' AND (
            (NEW.schema,NEW.event,NEW.level) IN (
                ('member_source','source_member_unusable','WARNING'),
                ('failure','family_engagement_failed','ERROR'))))
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
                ('failure','web_unhealthy','CRITICAL'))))) THEN
        RAISE EXCEPTION 'This login may not record this operational entry'
            USING ERRCODE = '42501';
    END IF;
    NEW.created_at := statement_timestamp();
    RETURN NEW;
END;
$$;

-- Refuse to commit unless the new body is installed behind the same
-- enabled BEFORE INSERT row trigger, still an invoker: the old body named
-- only the mail-dispatch and backup logins, the new one the scheduler too.
DO $check$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger t
        JOIN pg_proc p ON p.oid=t.tgfoid
        WHERE t.tgname='stewardship_operational_log_writer_v1'
          AND t.tgrelid='public.stewardship_operational_log'::regclass
          AND t.tgenabled='O' AND t.tgtype=7
          AND p.oid='public.stewardship_operational_log_writer_v1()'::regprocedure
          AND NOT p.prosecdef
          AND position('pk_stewardship_scheduler' IN p.prosrc)>0
          AND position('This login may not record this operational entry' IN p.prosrc)>0) THEN
        RAISE EXCEPTION 'Migration 0035 (operational log writer allow-list) is not installed as declared';
    END IF;
END
$check$;
