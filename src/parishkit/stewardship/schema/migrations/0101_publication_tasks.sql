-- Frozen forward migration file 0101 (the repository-wide file sequence):
-- the census publication tasks (#528, step 5, fifth slice). This file is
-- installed by its Django migration in the responses app and must never
-- change once released; tests/stewardship/test_schema_migration_files.py
-- pins its digest. Reversing needs its own forward migration.
--
-- Two task types run the publication that migrations 0087 (plan tables),
-- 0090 (the fenced worker's proposal states) and 0096 (the Administrator's
-- review) prepared: census_publication_preflight reads each Member with an
-- approved Automatic change and records a plan, and census_publication
-- writes one confirmed plan (responses/publication_tasks.py). Both run in
-- the general worker, on its source queue, and only the web login creates
-- them: an Administrator asks for a preflight, and confirming a plan
-- queues its run.
--
-- Only the two IMMUTABLE task-type maps change, each replaced with 0077's
-- body plus the two names: the execution map
-- (stewardship_task_type_login_v1, a baseline function, so functions.sql
-- carries the same body) and the creator map
-- (stewardship_task_type_creators_v1, created by 0041). Neither is
-- SECURITY DEFINER. No table, column, grant or other function changes. The
-- file ends with a DO block that refuses to commit unless both maps carry
-- the new names and keep the old ones.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

-- 0077's execution map with both census publication types run by the worker.
CREATE OR REPLACE FUNCTION public.stewardship_task_type_login_v1(task_type text) RETURNS text
    LANGUAGE sql IMMUTABLE STRICT
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$ SELECT CASE
        -- The login whose compiled registry executes each task type
        -- (runtime_background.py): general-queue types run in the worker,
        -- mail-queue types in mail dispatch. NULL is an unknown type.
        WHEN task_type IN (
            'activation_catchup',
            'automation_maintenance',
            'branding_cleanup',
            'campaign_boundary',
            'census_publication',
            'census_publication_preflight',
            'daily_digest_finalize',
            'daily_digest_prepare',
            'family_mail_prepare',
            'family_mail_test',
            'operational_collect',
            'operational_prepare',
            'operational_slack',
            'production_cleanup',
            'production_token_cleanup',
            'production_tokens',
            'report_exact_export',
            'report_export',
            'report_export_cleanup',
            'report_fact_verification',
            'report_facts',
            'security_prepare',
            'setup_finalize',
            'setup_source_cleanup',
            'setup_source_load',
            'source_refresh',
            'weekly_digest_finalize',
            'weekly_digest_prepare')
        THEN 'pk_stewardship_worker'
        WHEN task_type IN (
            'campaign_mail_test',
            'family_address_retry',
            'outbox_delivery',
            'setup_mail_test')
        THEN 'pk_stewardship_mail_dispatch'
    END $$;

-- 0077's creator map with both census publication types created by web.
CREATE OR REPLACE FUNCTION public.stewardship_task_type_creators_v1(task_type text) RETURNS text[]
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
        WHEN 'census_publication' THEN ARRAY['pk_stewardship_web']
        WHEN 'census_publication_preflight' THEN ARRAY['pk_stewardship_web']
        WHEN 'daily_digest_finalize' THEN ARRAY['pk_stewardship_scheduler','pk_stewardship_web']
        WHEN 'daily_digest_prepare' THEN ARRAY['pk_stewardship_scheduler','pk_stewardship_web']
        WHEN 'family_address_retry' THEN ARRAY[]::text[]
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

-- Refuse to commit unless both maps are installed as declared. It reads
-- only the catalog and the two IMMUTABLE maps, and writes nothing.
DO $check$
BEGIN
    IF public.stewardship_task_type_login_v1('census_publication') IS DISTINCT FROM 'pk_stewardship_worker'
       OR public.stewardship_task_type_login_v1('census_publication_preflight') IS DISTINCT FROM 'pk_stewardship_worker'
       OR public.stewardship_task_type_creators_v1('census_publication') IS DISTINCT FROM ARRAY['pk_stewardship_web']
       OR public.stewardship_task_type_creators_v1('census_publication_preflight') IS DISTINCT FROM ARRAY['pk_stewardship_web'] THEN
        RAISE EXCEPTION 'Migration 0101 (census publication tasks) is not installed as declared';
    END IF;
    -- A sample of the earlier entries is unchanged, an unknown type is
    -- still unknown, and both maps keep their attributes.
    IF public.stewardship_task_type_login_v1('family_address_retry') IS DISTINCT FROM 'pk_stewardship_mail_dispatch'
       OR public.stewardship_task_type_login_v1('source_refresh') IS DISTINCT FROM 'pk_stewardship_worker'
       OR public.stewardship_task_type_login_v1('unknown_type') IS NOT NULL
       OR public.stewardship_task_type_creators_v1('family_address_retry') IS DISTINCT FROM ARRAY[]::text[]
       OR public.stewardship_task_type_creators_v1('source_refresh') IS DISTINCT FROM
          ARRAY['pk_stewardship_scheduler','pk_stewardship_web','pk_stewardship_worker']
       OR public.stewardship_task_type_creators_v1('unknown_type') IS NOT NULL
       OR (SELECT count(*) FROM pg_proc p
           WHERE p.oid IN ('public.stewardship_task_type_login_v1(text)'::regprocedure,
                           'public.stewardship_task_type_creators_v1(text)'::regprocedure)
             AND NOT p.prosecdef AND p.provolatile='i' AND p.proisstrict
             AND p.proconfig=ARRAY['search_path=pg_catalog, public, pg_temp'])<>2 THEN
        RAISE EXCEPTION 'Migration 0101 (census publication tasks) is not installed as declared';
    END IF;
END
$check$;
