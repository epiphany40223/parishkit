-- Frozen forward migration file 0098 (the repository-wide file sequence):
-- the operator's debug-off clear names the switch it cleared (#1229). This
-- file is installed by its Django migration in the jobs app and must never
-- change once released; tests/stewardship/test_schema_migration_files.py
-- pins its digest. Reversing needs its own forward migration.
--
-- 0078's stewardship_debug_off_clear_v1() returns only how many rows it
-- cleared, and the admin-recovery login that runs it (pk-stewardship
-- debug-off-clear) cannot read the switch table, so the operator's
-- debug_logging_allowed audit event named no subject, while the
-- Administrator's Testing-only clear (0088) names the switch. This replaces
-- v1 with stewardship_debug_off_clear_v2(), the same clear (cleared_by
-- 'operator'; the guard sets the clear time), returning the id of the switch
-- it cleared, or NULL when none was set. 0078's insert guard admits one set
-- switch at a time, so there is at most one. SECURITY DEFINER, VOLATILE,
-- fixed search_path; PUBLIC has no EXECUTE. EXECUTE goes to the offline
-- admin-recovery login only, through database-grants
-- (runtime_grants.runtime_functions; CLOSURE_FUNCTIONS, since this follows
-- the users swap).
--
-- v1 is dropped rather than left behind: database-grants only adds grants,
-- so admin recovery's existing EXECUTE on v1 would otherwise stay and be
-- refused at admission as excess definer authority, and an ungranted v1
-- would be a dead definer that clears without an audit event. Dropping it
-- drops that grant with it. A release before this one calls v1, so its
-- debug-off-clear fails on this schema (fail forward).
--
-- The ALTER repeats SECURITY DEFINER, as every frozen definer does; the DO
-- block at the end refuses to commit unless v1 is gone and v2 is installed
-- as declared. No temporary objects: the migration login has no TEMP
-- privilege.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

DROP FUNCTION IF EXISTS public.stewardship_debug_off_clear_v1();

CREATE FUNCTION public.stewardship_debug_off_clear_v2() RETURNS uuid
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE
    cleared uuid;
BEGIN
    -- The guard sets the clear time; this names who cleared it.
    UPDATE public.stewardship_debug_off_switch
       SET cleared_at=statement_timestamp(), cleared_by='operator', cleared_actor_id=NULL
     WHERE cleared_at IS NULL
    RETURNING id INTO cleared;
    RETURN cleared;
END $$;
ALTER FUNCTION public.stewardship_debug_off_clear_v2() SECURITY DEFINER;
REVOKE ALL ON FUNCTION public.stewardship_debug_off_clear_v2() FROM PUBLIC;

-- Refuse to commit unless v1 is gone and v2 is the definer this file
-- installs. The missing-object test comes first, on its own, because
-- PostgreSQL does not short-circuit OR and the ::regprocedure cast below
-- would otherwise fail with its own error.
DO $check$
BEGIN
    IF to_regprocedure('public.stewardship_debug_off_clear_v1()') IS NOT NULL
       OR to_regprocedure('public.stewardship_debug_off_clear_v2()') IS NULL THEN
        RAISE EXCEPTION 'Migration 0098 (debug-off clear subject) is not installed as declared';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM pg_proc p
        WHERE p.oid='public.stewardship_debug_off_clear_v2()'::regprocedure
          AND p.prosecdef AND p.provolatile='v' AND NOT p.proretset
          AND p.prorettype='uuid'::regtype
          AND p.proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
          AND p.prosrc LIKE '%cleared_by=''operator''%'
          AND p.prosrc LIKE '%RETURNING id INTO cleared%')
       OR has_function_privilege('public',
            'public.stewardship_debug_off_clear_v2()', 'EXECUTE') THEN
        RAISE EXCEPTION 'Migration 0098 (debug-off clear subject) is not installed as declared';
    END IF;
END
$check$;
