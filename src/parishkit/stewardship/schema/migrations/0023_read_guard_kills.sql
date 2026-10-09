-- Frozen forward migration file 0023 (the repository-wide file sequence):
-- how many times an export's read guard stopped its worker at the deadline
-- (#386, L3). It is installed by the Django migration that names it in
-- FROZEN_SQL and must never change once released;
-- tests/stewardship/test_schema_migration_files.py pins its digest.
--
-- An export whose render always overruns its read guard hard-stops the
-- general worker (os._exit(70)) on every attempt, and recovery retried it up
-- to five times. Recovery may now give up after a second such stop, but the
-- evidence is the guard's own durable timeout entry (task_timed_out,
-- context what=read_guard, task_id), and the worker login may read only a
-- few columns of the operational log, not its context. This function counts
-- those entries for one task and returns only the count: SECURITY DEFINER
-- with a fixed search_path, callable by the general worker (EXECUTE comes
-- from database-grants, runtime_functions) and the schema owner alone.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

CREATE FUNCTION public.stewardship_read_guard_kills_v1(task uuid) RETURNS integer
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF session_user<>'pk_stewardship_worker' AND NOT pg_has_role(session_user,
        (SELECT nspowner FROM pg_namespace WHERE nspname='public'),'USAGE') THEN
        RAISE EXCEPTION 'Only the general worker counts read-guard stops' USING ERRCODE='42501';
    END IF;
    RETURN (SELECT count(*)::integer FROM public.stewardship_operational_log
             WHERE event='task_timed_out' AND schema='timeout'
               AND context->>'what'='read_guard' AND context->>'task_id'=task::text);
END $$;
-- Stated again on its own, as every definer is, so a later CREATE OR REPLACE
-- that omitted it would be caught by the check below and by the baseline's
-- attribute test.
ALTER FUNCTION public.stewardship_read_guard_kills_v1(uuid) SECURITY DEFINER;
REVOKE ALL ON FUNCTION public.stewardship_read_guard_kills_v1(uuid) FROM PUBLIC;

DO $check$
BEGIN
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname='stewardship_read_guard_kills_v1'
          AND pg_get_function_identity_arguments(p.oid)='task uuid'
          AND p.prosecdef
          AND p.provolatile='s'
          AND p.proconfig @> ARRAY['search_path=pg_catalog, public, pg_temp']
          AND p.prosrc LIKE '%context->>''what''=''read_guard''%'
          AND NOT has_function_privilege('public', p.oid, 'EXECUTE'))<>1 THEN
        RAISE EXCEPTION 'stewardship_read_guard_kills_v1 was not installed as declared';
    END IF;
END
$check$;
