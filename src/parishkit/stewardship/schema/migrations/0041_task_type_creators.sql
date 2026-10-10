-- Frozen forward migration file 0041 (the repository-wide file sequence):
-- each task type may be created only by the login(s) whose code creates it
-- (#389, the #350 residual). It is installed by the Django migration that
-- names it in FROZEN_SQL and must never change once released;
-- tests/stewardship/test_schema_migration_files.py pins its digest.
--
-- stewardship_task_login_guard_v1 binds claims and transitions to the login
-- that executes each type (#350), but let any non-owner login INSERT any
-- known type. So a web bug could queue scheduler-only maintenance or alert
-- work, and a compromised worker could queue Admin-only exports or setup
-- loads. A new map, stewardship_task_type_creators_v1, lists the logins
-- whose code creates each type (enqueue or explicit retry); the guard's
-- INSERT branch now refuses every other login. The Admin command line runs
-- as the web login. Mail dispatch creates no task.
--
-- The caller is identified as before: current_user, with the schema owner
-- exempt. SECURITY DEFINER bodies run as the owner, so they stay exempt and
-- their EXECUTE grants remain their binding; the only one that inserts a
-- task is stewardship_production_confirmation_effect_v1 (activation_catchup).
--
-- The guard's body is functions.sql's (no later migration replaced it) with
-- only the INSERT branch changed. Both functions are invoker's rights with a
-- fixed search_path, as the guard was; CREATE OR REPLACE resets attributes
-- it does not state, so each is stated here and checked below. Reversing
-- needs its own forward migration.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

CREATE FUNCTION public.stewardship_task_type_creators_v1(task_type text) RETURNS text[]
    LANGUAGE sql IMMUTABLE STRICT
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$ SELECT CASE task_type
        -- The logins whose code creates each type, by enqueue or explicit
        -- retry (tests/stewardship/test_task_creators.py pins every call
        -- site). NULL is an unknown type.
        WHEN 'activation_catchup' THEN ARRAY['pk_stewardship_web']
        WHEN 'automation_maintenance' THEN ARRAY['pk_stewardship_scheduler']
        WHEN 'branding_cleanup' THEN ARRAY['pk_stewardship_scheduler']
        WHEN 'campaign_boundary' THEN ARRAY['pk_stewardship_scheduler']
        WHEN 'campaign_mail_test' THEN ARRAY['pk_stewardship_web']
        WHEN 'daily_digest_finalize' THEN ARRAY['pk_stewardship_scheduler','pk_stewardship_web']
        WHEN 'daily_digest_prepare' THEN ARRAY['pk_stewardship_scheduler','pk_stewardship_web']
        WHEN 'family_mail_prepare' THEN ARRAY['pk_stewardship_scheduler','pk_stewardship_web']
        WHEN 'family_mail_test' THEN ARRAY['pk_stewardship_web']
        WHEN 'operational_collect' THEN ARRAY['pk_stewardship_scheduler']
        WHEN 'operational_prepare' THEN ARRAY['pk_stewardship_scheduler']
        WHEN 'operational_slack' THEN ARRAY['pk_stewardship_scheduler']
        WHEN 'outbox_delivery' THEN ARRAY['pk_stewardship_web','pk_stewardship_worker']
        WHEN 'production_cleanup' THEN ARRAY['pk_stewardship_web']
        WHEN 'production_token_cleanup' THEN ARRAY['pk_stewardship_scheduler','pk_stewardship_web']
        WHEN 'production_tokens' THEN ARRAY['pk_stewardship_scheduler','pk_stewardship_web']
        WHEN 'report_exact_export' THEN ARRAY['pk_stewardship_web']
        WHEN 'report_export' THEN ARRAY['pk_stewardship_web','pk_stewardship_worker']
        WHEN 'report_export_cleanup' THEN ARRAY['pk_stewardship_scheduler','pk_stewardship_web']
        WHEN 'report_fact_verification' THEN ARRAY['pk_stewardship_scheduler']
        WHEN 'report_facts' THEN ARRAY['pk_stewardship_scheduler']
        WHEN 'security_prepare' THEN ARRAY['pk_stewardship_scheduler']
        WHEN 'setup_finalize' THEN ARRAY['pk_stewardship_scheduler']
        WHEN 'setup_mail_test' THEN ARRAY['pk_stewardship_web']
        WHEN 'setup_source_cleanup' THEN ARRAY['pk_stewardship_scheduler']
        WHEN 'setup_source_load' THEN ARRAY['pk_stewardship_web']
        WHEN 'source_refresh' THEN ARRAY['pk_stewardship_scheduler','pk_stewardship_web','pk_stewardship_worker']
        WHEN 'weekly_digest_finalize' THEN ARRAY['pk_stewardship_scheduler','pk_stewardship_web']
        WHEN 'weekly_digest_prepare' THEN ARRAY['pk_stewardship_scheduler','pk_stewardship_web']
    END $$;

CREATE OR REPLACE FUNCTION public.stewardship_task_login_guard_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
BEGIN
    -- Bind task writes to the database login, not to the caller-supplied
    -- actor_id: the execution guard trusts actor_id = worker_id, so without
    -- this check any login holding UPDATE could claim, finish or cancel
    -- another service's task. The schema owner (migration and SECURITY
    -- DEFINER bodies, such as the delivery recovery commands) is exempt.
    IF pg_has_role(current_user, (SELECT nspowner FROM pg_namespace
            WHERE nspname='public'), 'USAGE') THEN
        RETURN NEW;
    END IF;
    IF TG_OP = 'INSERT' THEN
        -- Web, worker and scheduler create tasks for other services, but
        -- each type only from the logins whose code creates it (#389);
        -- only its owner may execute it.
        IF public.stewardship_task_type_login_v1(NEW.task_type) IS NULL THEN
            RAISE EXCEPTION 'Unknown task type' USING ERRCODE='42501';
        END IF;
        IF NOT current_user::text = ANY(
                public.stewardship_task_type_creators_v1(NEW.task_type)) THEN
            RAISE EXCEPTION 'This login cannot create this task type'
                USING ERRCODE='42501';
        END IF;
        RETURN NEW;
    END IF;
    -- The service whose compiled registry executes this type may claim,
    -- heartbeat, finish, expire and recover it (the execution guard still
    -- checks the fence and lease).
    IF current_user = public.stewardship_task_type_login_v1(OLD.task_type) THEN
        RETURN NEW;
    END IF;
    -- Web only creates work and cancels an Admin's waiting cleanup; it never
    -- claims, executes or recovers any task.
    IF current_user = 'pk_stewardship_web'
       AND OLD.task_type = 'production_cleanup'
       AND OLD.state IN ('queued', 'retry_wait')
       AND NEW.state = 'cancelled' AND NEW.action = 'safe_cancel' THEN
        RETURN NEW;
    END IF;
    -- The scheduler only retires superseded waiting source work while it
    -- holds its session and source-cancellation locks.
    IF current_user = 'pk_stewardship_scheduler'
       AND OLD.task_type = 'source_refresh'
       AND OLD.state IN ('queued', 'retry_wait', 'abandoned')
       AND NEW.state = 'cancelled'
       AND NEW.action IN ('safe_cancel', 'recovery_cancel')
       AND NEW.lease_expires_at IS NULL
       AND EXISTS(SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
            AND locktype='advisory' AND classid=736229 AND objid=1
            AND objsubid=2 AND granted)
       AND EXISTS(SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
            AND locktype='advisory' AND classid=736220 AND objid=1
            AND objsubid=2 AND mode='ExclusiveLock' AND granted) THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'This login cannot change this task' USING ERRCODE='42501';
END $$;

-- Refuse to commit unless everything above is installed as declared.
DO $check$
DECLARE
    missing text;
BEGIN
    -- Both are invoker's rights with the fixed search_path.
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public'
          AND ((p.proname='stewardship_task_type_creators_v1'
                AND pg_get_function_identity_arguments(p.oid)='task_type text'
                AND p.prorettype='text[]'::regtype
                AND p.provolatile='i' AND p.proisstrict)
            OR (p.proname='stewardship_task_login_guard_v1'
                AND pg_get_function_identity_arguments(p.oid)=''
                AND p.prorettype='trigger'::regtype
                AND p.prosrc LIKE '%public.stewardship_task_type_creators_v1(NEW.task_type)%'
                AND p.prosrc LIKE '%This login cannot create this task type%'))
          AND NOT p.prosecdef
          AND p.proconfig=ARRAY['search_path=pg_catalog, public, pg_temp'])<>2 THEN
        RAISE EXCEPTION 'Migration 0041 (task type creators) is not installed as declared';
    END IF;
    -- Every type the execution map knows has at least one creator, and the
    -- guard is still attached before INSERT and UPDATE.
    SELECT string_agg(t, ', ') INTO missing
    FROM unnest(ARRAY[
        'activation_catchup','automation_maintenance','branding_cleanup',
        'campaign_boundary','campaign_mail_test','daily_digest_finalize',
        'daily_digest_prepare','family_mail_prepare','family_mail_test',
        'operational_collect','operational_prepare','operational_slack',
        'outbox_delivery','production_cleanup','production_token_cleanup',
        'production_tokens','report_exact_export','report_export',
        'report_export_cleanup','report_fact_verification','report_facts',
        'security_prepare','setup_finalize','setup_mail_test',
        'setup_source_cleanup','setup_source_load','source_refresh',
        'weekly_digest_finalize','weekly_digest_prepare']) t
    WHERE public.stewardship_task_type_login_v1(t) IS NULL
       OR coalesce(cardinality(public.stewardship_task_type_creators_v1(t)),0)=0;
    IF missing IS NOT NULL THEN
        RAISE EXCEPTION 'Migration 0041: task types without an executor or creator: %', missing;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger t
                   WHERE t.tgrelid='public.stewardship_task_run'::regclass
                     AND t.tgname='stewardship_task_login_guard_v1'
                     AND NOT t.tgisinternal AND t.tgenabled='O'
                     AND t.tgfoid='public.stewardship_task_login_guard_v1()'::regprocedure) THEN
        RAISE EXCEPTION 'Migration 0041: the task login guard is not attached';
    END IF;
END
$check$;
