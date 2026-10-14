-- Frozen forward migration file 0086 (the repository-wide file sequence): a
-- source refresh refreshes its staged snapshot's planner statistics before
-- the ready transition, outside the global work-order lock (#895, change 8).
-- This file is installed by its Django migration in the source app and must
-- never change once released; tests/stewardship/test_schema_migration_files.py
-- pins its digest. Reversing needs its own forward migration.
--
-- The ready transition holds the global work-order lock (736220,1). Its
-- snapshot guard re-derives the corpus evidence from the membership
-- (stewardship_source_corpus_evidence) BEFORE the row is updated, while the
-- statistics trigger (stewardship_source_snapshot_statistics_v1) analyzes
-- the membership tables only AFTER it. The guard's queries are therefore
-- planned with statistics that know nothing of the new snapshot's rows: at
-- 1,100 Families the ready transition held the lock for about 1 s instead
-- of about 0.1 s with fresh statistics.
--
-- stewardship_source_snapshot_analyze_v1(snapshot) analyzes the same nine
-- membership tables as that trigger. A source refresh calls it in a source
-- step after its last staging batch, so validation and the guard both plan
-- with statistics that include the snapshot. ANALYZE needs table ownership
-- (or MAINTAIN, which would also allow LOCK TABLE in any mode), so it is a
-- SECURITY DEFINER owned by the schema owner, as the trigger function is;
-- no runtime login gains ANALYZE or MAINTAIN authority. Only the general
-- worker login (or a member of the schema owner) may call it, and only for a
-- snapshot that is still staging with its live fenced owner (the membership
-- guard's predicate), so it runs at most during a refresh in progress.
-- ANALYZE takes SHARE UPDATE EXCLUSIVE on each table until commit, which no
-- read, write or row lock conflicts with. The trigger is unchanged and still
-- analyzes at completion, for setup loading and as a safety net.
--
-- The DO block at the end refuses to commit unless the definer is installed
-- as declared, its owner owns the nine tables, and the trigger is still
-- attached. No temporary objects: the migration login has no TEMP privilege.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

CREATE FUNCTION public.stewardship_source_snapshot_analyze_v1(snapshot uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF session_user<>'pk_stewardship_worker' AND NOT pg_has_role(session_user,
        (SELECT nspowner FROM pg_namespace WHERE nspname='public'),'USAGE') THEN
        RAISE EXCEPTION 'Only the general worker refreshes snapshot statistics'
            USING ERRCODE='42501';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM public.stewardship_source_snapshot s
        JOIN public.stewardship_source_lease l ON l.owner_id=s.task_id
          AND l.fence=s.source_fence AND l.phase IN ('full','delta')
        JOIN public.stewardship_task_run t ON t.id=l.owner_id
        WHERE s.id=snapshot AND s.state='staging'
          AND l.expires_at > clock_timestamp() AND t.state='running'
          AND t.fence=l.task_fence AND t.worker_id=l.worker_id
          AND t.lease_expires_at > clock_timestamp()
    ) THEN
        RAISE EXCEPTION 'Snapshot statistics require live staging ownership'
            USING ERRCODE='23514';
    END IF;
    ANALYZE public.stewardship_snapshot_family, public.stewardship_snapshot_member,
        public.stewardship_snapshot_contact, public.stewardship_snapshot_address,
        public.stewardship_snapshot_ministry, public.stewardship_snapshot_roster,
        public.stewardship_snapshot_fund, public.stewardship_snapshot_pledge,
        public.stewardship_snapshot_contribution;
    RETURN true;
END $$;
-- Stated again on its own, as every definer is.
ALTER FUNCTION public.stewardship_source_snapshot_analyze_v1(uuid) SECURITY DEFINER;
REVOKE ALL ON FUNCTION public.stewardship_source_snapshot_analyze_v1(uuid) FROM PUBLIC;

-- Refuse to commit unless the definer is installed as declared (a definer
-- with the fixed search_path, the caller and staging checks, all nine
-- tables, and no PUBLIC execute), its owner owns all nine tables (ANALYZE
-- by anyone else only warns and skips the table, so the definer would
-- quietly do nothing), and the completion trigger is still attached. It
-- reads only the catalog. The missing function is checked
-- first, on its own, because PostgreSQL does not short-circuit OR and the
-- ::regprocedure casts below would otherwise fail with their own error.
DO $check$
BEGIN
    IF to_regprocedure('public.stewardship_source_snapshot_analyze_v1(uuid)') IS NULL
       OR to_regprocedure('public.stewardship_source_snapshot_statistics_v1()') IS NULL THEN
        RAISE EXCEPTION 'Migration 0086 (snapshot statistics) is not installed as declared';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_proc p
           WHERE p.oid='public.stewardship_source_snapshot_analyze_v1(uuid)'::regprocedure
             AND p.prosecdef AND p.provolatile='v' AND NOT p.proretset
             AND p.prorettype='boolean'::regtype
             AND p.proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
             AND p.prosrc LIKE '%session_user<>''pk_stewardship_worker''%'
             AND p.prosrc LIKE '%s.state=''staging''%'
             AND p.prosrc LIKE '%t.lease_expires_at > clock_timestamp()%'
             AND p.prosrc LIKE '%ANALYZE public.stewardship_snapshot_family,%'
             AND p.prosrc LIKE '%public.stewardship_snapshot_contribution;%')
       OR EXISTS (SELECT 1 FROM pg_proc p, pg_class c
           WHERE p.oid='public.stewardship_source_snapshot_analyze_v1(uuid)'::regprocedure
             AND c.oid IN ('public.stewardship_snapshot_family'::regclass,
                 'public.stewardship_snapshot_member'::regclass,
                 'public.stewardship_snapshot_contact'::regclass,
                 'public.stewardship_snapshot_address'::regclass,
                 'public.stewardship_snapshot_ministry'::regclass,
                 'public.stewardship_snapshot_roster'::regclass,
                 'public.stewardship_snapshot_fund'::regclass,
                 'public.stewardship_snapshot_pledge'::regclass,
                 'public.stewardship_snapshot_contribution'::regclass)
             AND NOT pg_has_role(p.proowner, c.relowner, 'USAGE'))
       OR has_function_privilege('public',
           'public.stewardship_source_snapshot_analyze_v1(uuid)', 'EXECUTE')
       OR NOT EXISTS (SELECT 1 FROM pg_trigger t
           WHERE t.tgrelid='public.stewardship_source_snapshot'::regclass
             AND t.tgname='stewardship_source_snapshot_statistics_v1'
             AND NOT t.tgisinternal AND t.tgenabled='O'
             AND t.tgfoid='public.stewardship_source_snapshot_statistics_v1()'::regprocedure) THEN
        RAISE EXCEPTION 'Migration 0086 (snapshot statistics) is not installed as declared';
    END IF;
END
$check$;
