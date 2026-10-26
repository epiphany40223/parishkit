-- Frozen forward migration file 0184 (the repository-wide file sequence,
-- assigned by the coordinator): the campaign statistics count the pledge
-- values in force (#2202; reports spec, "Values in force on Financial
-- stewardship"). This file is installed by its Django migration in the
-- reports app and must never change once released;
-- tests/stewardship/test_schema_migration_files.py pins its digest.
-- Reversing needs its own forward migration.
--
-- The Administrator decided (2026-10-25) that the Response dashboard's
-- pledge totals use each Family's values in force, as Financial
-- stewardship does since 0182: a reviewer's Edit of the annual pledge while
-- that Edit is in force, else the Family's own answer. The statistics
-- capture (reports/statistics_selection.py) is read by the web login and by
-- the general worker, which may read only a response's annual_pledge, never
-- its answers or the pledge review rows that 0181's
-- stewardship_pledge_review_states_v1 reads. So this file adds one narrow
-- SECURITY DEFINER, stewardship_pledge_edits_in_force_v1(campaign), that
-- returns only, for each Family with an Edit in force, the response it is
-- in force on and the Edit's annual pledge. It finds the Edit exactly as
-- 0182's Financial selection does (its edits step): the latest review row
-- that stewardship_pledge_review_states_v1, the selection that places
-- Pledges' stages, holds in force, with the decision Accepted (edited).
-- It has a fixed search_path; PUBLIC has no EXECUTE, and database-grants
-- grants it to the web and general worker logins only
-- (runtime_grants.runtime_functions); the body also refuses any other
-- session login that is not a member of the schema owner, as the campaign
-- comparison function (0067) does.
--
-- The DO block at the end refuses to commit unless the definer is installed
-- as declared (its body and settings, the selection's owner, and no grant
-- yet) and its selection runs. No row, grant or credential changes, and no Family code, link or
-- session is touched. No temporary objects.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

CREATE FUNCTION public.stewardship_pledge_edits_in_force_v1(campaign_uuid uuid)
RETURNS TABLE(family_id uuid, submission_id uuid, annual_pledge numeric)
LANGUAGE plpgsql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF session_user NOT IN ('pk_stewardship_web','pk_stewardship_worker') AND NOT pg_has_role(session_user,
        (SELECT nspowner FROM pg_namespace WHERE nspname='public'),'USAGE') THEN
        RAISE EXCEPTION 'Only the web and worker logins read the pledge Edits in force' USING ERRCODE='42501';
    END IF;
    -- 0182's edits step: the Family's latest pledge review, when 0181's
    -- selection holds it in force and its decision is Accepted (edited).
    -- Entered by hand and the undo of a terminal step carry that decision
    -- with its values, so they count too; Back to review of a decision,
    -- Accept, Ignore and a newer, different pledge answer from the Family
    -- leave none.
    RETURN QUERY
    SELECT k.family_id,k.submission_id,v.reviewed_annual_pledge::numeric
    FROM public.stewardship_pledge_review_states_v1(campaign_uuid,NULL) k
    JOIN public.stewardship_pledge_review v ON v.id=k.review_id
    WHERE k.in_force AND v.decision='accepted_edited';
END $$;
ALTER FUNCTION public.stewardship_pledge_edits_in_force_v1(uuid) SECURITY DEFINER;
REVOKE ALL ON FUNCTION public.stewardship_pledge_edits_in_force_v1(uuid) FROM PUBLIC;

DO $check$
BEGIN
    IF to_regprocedure('public.stewardship_pledge_edits_in_force_v1(uuid)') IS NULL
       OR (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
           WHERE n.nspname='public' AND p.proname='stewardship_pledge_edits_in_force_v1')<>1 THEN
        RAISE EXCEPTION 'Migration 0184 (statistics in force) is not installed as declared';
    END IF;
    -- A stable definer set-returning function with its pinned search_path,
    -- the login check and 0182's selection; CREATE OR REPLACE would drop
    -- SECURITY DEFINER, so prosecdef is checked here.
    IF NOT EXISTS (SELECT 1 FROM pg_proc p
           WHERE p.oid='public.stewardship_pledge_edits_in_force_v1(uuid)'::regprocedure
             AND p.prosecdef AND p.provolatile='s' AND p.proretset
             AND p.prolang=(SELECT oid FROM pg_language WHERE lanname='plpgsql')
             AND p.proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
             AND p.prosrc LIKE '%session_user NOT IN (''pk_stewardship_web'',''pk_stewardship_worker'')%'
             AND p.prosrc LIKE '%AND NOT pg_has_role(session_user,%'
             AND p.prosrc LIKE '%FROM public.stewardship_pledge_review_states_v1(campaign_uuid,NULL) k%'
             AND p.prosrc LIKE '%WHERE k.in_force AND v.decision=''accepted_edited'';%') THEN
        RAISE EXCEPTION 'Migration 0184 (statistics in force) is not installed as declared';
    END IF;
    -- Owned as the selection it reads is (so it runs with no other
    -- authority), with no EXECUTE for PUBLIC and no grant at all yet:
    -- database-grants adds the web and worker logins' afterwards.
    IF (SELECT p.proowner FROM pg_proc p
            WHERE p.oid='public.stewardship_pledge_edits_in_force_v1(uuid)'::regprocedure)
            IS DISTINCT FROM (SELECT p.proowner FROM pg_proc p
            WHERE p.oid='public.stewardship_pledge_review_states_v1(uuid,uuid)'::regprocedure)
       OR has_function_privilege('public',
           'public.stewardship_pledge_edits_in_force_v1(uuid)', 'EXECUTE')
       OR EXISTS (SELECT 1 FROM pg_proc p CROSS JOIN LATERAL aclexplode(p.proacl) a
           WHERE p.oid='public.stewardship_pledge_edits_in_force_v1(uuid)'::regprocedure
             AND a.grantee<>p.proowner) THEN
        RAISE EXCEPTION 'Migration 0184 (statistics in force) has unexpected owner or grants';
    END IF;
    -- The selection plans and runs (a column or function it names that is
    -- missing fails here): a campaign that does not exist has no Edit.
    IF EXISTS (SELECT 1 FROM public.stewardship_pledge_edits_in_force_v1(
            '00000000-0000-0000-0000-000000000000')) THEN
        RAISE EXCEPTION 'Migration 0184 (statistics in force) cannot read the selection';
    END IF;
END
$check$;
