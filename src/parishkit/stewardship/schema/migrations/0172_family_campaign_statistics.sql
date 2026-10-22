-- Frozen forward migration file 0172 (the repository-wide file sequence):
-- a new campaign's first load refreshes the Family campaign tables' planner
-- statistics as soon as it commits (#1932). This file is installed by its
-- Django migration in the campaigns app and must never change once
-- released; tests/stewardship/test_schema_migration_files.py pins its
-- digest. Reversing needs its own forward migration.
--
-- A new campaign's first load (the general worker's source promotion)
-- bulk-inserts the campaign's stewardship_family_campaign rows and their
-- stewardship_family_eligibility history. The scheduler reads its Family
-- planning inputs within about 15 s. Until autovacuum re-analyzes the
-- tables, their statistics describe only earlier campaigns, so the planner
-- estimates one row for the new campaign_id and re-aggregates the history
-- once per Family: 0.89 s instead of 0.01 s at 2,700 Families, and
-- quadratic in the population.
--
-- stewardship_family_campaign_analyze_v1(campaign) analyzes those two
-- tables. The worker calls it once, after the promotion that inserted the
-- campaign's new Family rows commits, never inside it. ANALYZE needs table
-- ownership (or MAINTAIN, which would also allow LOCK TABLE in any mode,
-- VACUUM, REINDEX and CLUSTER), so, as 0086's snapshot definer is, it is a
-- SECURITY DEFINER owned by the schema owner; no runtime login gains
-- ANALYZE or MAINTAIN authority. Only the general worker login (or a member
-- of the schema owner) may call it, and only for a campaign that can still
-- receive Family effects (draft, scheduled, active or closed). ANALYZE
-- takes SHARE UPDATE EXCLUSIVE on each table until commit, which no read,
-- write or row lock conflicts with.
--
-- The DO block at the end refuses to commit unless the definer is installed
-- as declared and its owner owns both tables. No temporary objects: the
-- migration login has no TEMP privilege.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

CREATE FUNCTION public.stewardship_family_campaign_analyze_v1(campaign uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF session_user<>'pk_stewardship_worker' AND NOT pg_has_role(session_user,
        (SELECT nspowner FROM pg_namespace WHERE nspname='public'),'USAGE') THEN
        RAISE EXCEPTION 'Only the general worker refreshes Family statistics'
            USING ERRCODE='42501';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM public.stewardship_campaign c
        WHERE c.id=campaign
          AND c.state IN ('draft','scheduled','active','closed')
    ) THEN
        RAISE EXCEPTION 'Family statistics require a campaign that takes Family effects'
            USING ERRCODE='23514';
    END IF;
    ANALYZE public.stewardship_family_campaign, public.stewardship_family_eligibility;
    RETURN true;
END $$;
-- Stated again on its own, as every definer is.
ALTER FUNCTION public.stewardship_family_campaign_analyze_v1(uuid) SECURITY DEFINER;
REVOKE ALL ON FUNCTION public.stewardship_family_campaign_analyze_v1(uuid) FROM PUBLIC;

-- Refuse to commit unless the definer is installed as declared (a definer
-- with the fixed search_path, the caller and campaign checks, both tables,
-- and no PUBLIC execute) and its owner owns both tables (ANALYZE by anyone
-- else only warns and skips the table, so the definer would quietly do
-- nothing). It reads only the catalog. The missing function is checked
-- first, on its own, because PostgreSQL does not short-circuit OR and the
-- ::regprocedure casts below would otherwise fail with their own error.
DO $check$
BEGIN
    IF to_regprocedure('public.stewardship_family_campaign_analyze_v1(uuid)') IS NULL THEN
        RAISE EXCEPTION 'Migration 0172 (Family campaign statistics) is not installed as declared';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_proc p
           WHERE p.oid='public.stewardship_family_campaign_analyze_v1(uuid)'::regprocedure
             AND p.prosecdef AND p.provolatile='v' AND NOT p.proretset
             AND p.prorettype='boolean'::regtype
             AND p.proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
             AND p.prosrc LIKE '%session_user<>''pk_stewardship_worker''%'
             AND p.prosrc LIKE '%c.state IN (''draft'',''scheduled'',''active'',''closed'')%'
             AND p.prosrc LIKE '%ANALYZE public.stewardship_family_campaign, public.stewardship_family_eligibility;%')
       OR EXISTS (SELECT 1 FROM pg_proc p, pg_class c
           WHERE p.oid='public.stewardship_family_campaign_analyze_v1(uuid)'::regprocedure
             AND c.oid IN ('public.stewardship_family_campaign'::regclass,
                 'public.stewardship_family_eligibility'::regclass)
             AND NOT pg_has_role(p.proowner, c.relowner, 'USAGE'))
       OR has_function_privilege('public',
           'public.stewardship_family_campaign_analyze_v1(uuid)', 'EXECUTE') THEN
        RAISE EXCEPTION 'Migration 0172 (Family campaign statistics) is not installed as declared';
    END IF;
END
$check$;
