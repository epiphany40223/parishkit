-- Frozen forward migration file 0177 (the repository-wide file sequence):
-- the setup load's organization check runs with its owner's rights (#2163).
-- This file is installed by the Django migration that names it in
-- FROZEN_SQL and must never change once released;
-- tests/stewardship/test_schema_migration_files.py pins its digest.
--
-- 0174 (#1554) made stewardship_setup_load_organization_v1, a BEFORE UPDATE
-- trigger on stewardship_setup_attempt, run with its caller's rights. It
-- reads stewardship_setup_parishsoft_lookup, which only web and the
-- ParishSoft installer may read. Under a custom plan the condition folds to
-- false and its subqueries are never opened, but once PostgreSQL switches a
-- connection to the generic plan, executor startup checks SELECT on both
-- tables, so a scheduler or worker update of a setup attempt (the expiry
-- sweep, the setup load's completion) fails with "permission denied".
--
-- This file replaces the function with 0174's body, unchanged except for one
-- comment naming this issue, as SECURITY DEFINER with the same fixed
-- search_path. CREATE OR REPLACE keeps its owner and grants; EXECUTE stays
-- revoked from PUBLIC. Granting the scheduler and worker SELECT on the lookup
-- table instead was rejected: no other login may read other organizations'
-- names (#1554). No stored row changes.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

-- The setup load never starts for a staged ParishSoft key with no chosen
-- organization, nor while an organization lookup is still open. (The web
-- also requires a staged key before it queues the load at all.)
CREATE OR REPLACE FUNCTION public.stewardship_setup_load_organization_v1() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    -- SECURITY DEFINER (#2163): the scheduler and worker also update setup
    -- attempts, and may not read the lookup table themselves.
    IF NEW.state='loading' AND OLD.state IS DISTINCT FROM 'loading' AND (
        EXISTS (SELECT 1 FROM public.stewardship_setup_sealed_credential c
            WHERE c.attempt_id=NEW.id AND c.target='parishsoft' AND c.scrubbed_at IS NULL
              AND NOT c.settings ? 'organization_id')
        OR EXISTS (SELECT 1 FROM public.stewardship_setup_parishsoft_lookup l
            WHERE l.attempt_id=NEW.id AND l.state IN ('queued','running','ready','failed'))) THEN
        RAISE EXCEPTION 'The setup load needs a chosen ParishSoft organization (#1554)'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
ALTER FUNCTION public.stewardship_setup_load_organization_v1() SECURITY DEFINER;
REVOKE ALL ON FUNCTION public.stewardship_setup_load_organization_v1() FROM PUBLIC;

DO $check$
BEGIN
    -- The function runs with its owner's rights and the fixed search path,
    -- PUBLIC cannot execute it, and its body is exactly this file's (its
    -- md5), so both of 0174's checks and the #2163 comment are present.
    IF NOT EXISTS (SELECT 1 FROM pg_proc p
        WHERE p.oid=to_regprocedure('public.stewardship_setup_load_organization_v1()')
          AND p.prosecdef
          AND p.proconfig @> ARRAY['search_path=pg_catalog, public, pg_temp']
          AND NOT has_function_privilege('public', p.oid, 'EXECUTE')
          AND md5(p.prosrc)='b55a0122e701a3eb5f0ff2f75700d039') THEN
        RAISE EXCEPTION 'Migration 0177: stewardship_setup_load_organization_v1 is not installed as declared';
    END IF;
    -- 0174's trigger still calls it, enabled.
    IF NOT EXISTS (SELECT 1 FROM pg_trigger t
        WHERE t.tgrelid='public.stewardship_setup_attempt'::regclass
          AND t.tgname='stewardship_setup_load_organization'
          AND t.tgfoid=to_regprocedure('public.stewardship_setup_load_organization_v1()')
          AND NOT t.tgisinternal AND t.tgenabled='O') THEN
        RAISE EXCEPTION 'Migration 0177: the setup load organization trigger is missing';
    END IF;
END
$check$;
