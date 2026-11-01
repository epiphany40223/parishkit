-- Frozen forward migration file 0199 (the repository-wide file sequence,
-- assigned by the coordinator): the hourly maintenance task deletes ended
-- Family sessions and expired anonymous Django sessions (#2362). This file
-- is installed by its Django migration in the accounts app and must never
-- change once released; tests/stewardship/test_schema_migration_files.py
-- pins its digest. Reversing needs its own forward migration.
--
-- sessions.cleanup_family_sessions and cleanup_anonymous_sessions ran only
-- in tests, so nothing deleted an ended Family session row, its Django
-- session (whose key is the Family's cookie value) or an expired anonymous
-- Django session in production. The general worker, which runs the hourly
-- maintenance task, may never read a session key (the same rule as for
-- Admin sessions, stewardship_admin_session_purge_v1 in 0004), so this file
-- adds two SECURITY DEFINER functions that find the keys themselves and
-- return only a count:
--
--   stewardship_family_session_purge_v1(uuid[])
--       for the named Family sessions that have ended (revoked, past their
--       four-hour deadline or idle for 60 minutes), except a Testing
--       session that a sealed go-live cleanup inventory owns: cancels their
--       open forms and releases each form's source pin (what
--       responses.baselines.cancel_session_baselines does for logout), then
--       deletes the session rows and their Django sessions
--   stewardship_anonymous_session_purge_v1(integer)
--       deletes up to that many (1 to 1000) expired Django sessions that no
--       Admin or Family session row names: OAuth sign-ins never finished
--       and visits that never signed in
--
-- Both refuse any session login other than the general worker or a member
-- of the schema owner, pin their search_path and have no EXECUTE for
-- PUBLIC; database-grants grants both to the general worker only
-- (runtime_grants.runtime_functions). The definer cancels the forms itself
-- because the source pin guard admits no worker release of a form's pin.
-- A session that simply lapsed is deleted without a revocation and
-- without a back-dated family_session_ended audit event; a revoked one
-- already has its event.
--
-- The DO block at the end refuses to commit unless both definers are
-- installed as declared. No row, grant or credential changes here; the
-- first maintenance run after the upgrade clears the backlog.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

CREATE FUNCTION public.stewardship_family_session_purge_v1(sessions uuid[]) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE
    ended uuid[];
    removed integer;
BEGIN
    IF session_user<>'pk_stewardship_worker' AND NOT pg_has_role(session_user,
        (SELECT nspowner FROM pg_namespace WHERE nspname='public'),'USAGE') THEN
        RAISE EXCEPTION 'Only the maintenance task purges Family sessions' USING ERRCODE='42501';
    END IF;
    -- The named rows that have ended, as accounts/session_policy.py's
    -- FAMILY_IDLE and the deadline decide it, locked in id order. A Testing
    -- session that a sealed go-live inventory owns is left to that
    -- cleanup's own worker, as cleanup_family_sessions leaves it.
    SELECT coalesce(array_agg(chosen.id), '{}') INTO ended FROM (
        SELECT f.id FROM public.stewardship_family_session f
        WHERE f.id=ANY(sessions)
          AND (f.revoked_at IS NOT NULL OR f.expires_at<=statement_timestamp()
               OR f.last_activity_at<=statement_timestamp()-interval '60 minutes')
          AND NOT (f.mode='testing' AND EXISTS (
              SELECT 1 FROM public.stewardship_family_campaign family
              JOIN public.stewardship_campaign_credentials credentials
                ON credentials.campaign_id=family.campaign_id
              WHERE family.id=f.family_id AND credentials.go_live_gate))
        ORDER BY f.id
        FOR UPDATE OF f) chosen;
    -- Their open forms end as cancelled and each releases its own source
    -- pin, as end_baseline does; the form and pin guards check both (the
    -- caller holds the work-order lock they require).
    WITH cancelled AS (
        UPDATE public.stewardship_family_form_baseline b
        SET state='cancelled', ended_at=statement_timestamp(), version=b.version+1
        WHERE b.family_session_id=ANY(ended) AND b.state='open'
        RETURNING b.id
    )
    DELETE FROM public.stewardship_source_pin p USING cancelled
    WHERE p.parent_kind='form_baseline' AND p.parent_id=cancelled.id;
    -- Then the rows and their Django sessions, in one statement so the
    -- deferred foreign key holds; only the count leaves this function.
    WITH gone AS (
        DELETE FROM public.stewardship_family_session f WHERE f.id=ANY(ended)
        RETURNING f.session_id
    ), keys AS (
        DELETE FROM public.django_session d USING gone
        WHERE d.session_key=gone.session_id
        RETURNING d.session_key
    )
    SELECT count(*) INTO removed FROM gone;
    RETURN removed;
END $$;
ALTER FUNCTION public.stewardship_family_session_purge_v1(uuid[]) SECURITY DEFINER;
REVOKE ALL ON FUNCTION public.stewardship_family_session_purge_v1(uuid[]) FROM PUBLIC;

CREATE FUNCTION public.stewardship_anonymous_session_purge_v1(batch integer) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE removed integer;
BEGIN
    IF session_user<>'pk_stewardship_worker' AND NOT pg_has_role(session_user,
        (SELECT nspowner FROM pg_namespace WHERE nspname='public'),'USAGE') THEN
        RAISE EXCEPTION 'Only the maintenance task purges anonymous sessions' USING ERRCODE='42501';
    END IF;
    IF batch IS NULL OR batch<1 OR batch>1000 THEN
        RAISE EXCEPTION 'Session cleanup requires a bounded batch size' USING ERRCODE='22023';
    END IF;
    -- Expired Django sessions no Admin or Family session names (an Admin
    -- row names its session by the stored digest, a Family row by the
    -- key), oldest first, skipping any another transaction holds.
    WITH doomed AS (
        SELECT d.session_key FROM public.django_session d
        WHERE d.expire_date<=statement_timestamp()
          AND NOT EXISTS (SELECT 1 FROM public.stewardship_portal_session p
                          WHERE p.session_id=d.session_key)
          AND NOT EXISTS (SELECT 1 FROM public.stewardship_family_session f
                          WHERE f.session_id=d.session_key)
        ORDER BY d.expire_date, d.session_key
        LIMIT batch
        FOR UPDATE OF d SKIP LOCKED
    ), gone AS (
        DELETE FROM public.django_session d USING doomed
        WHERE d.session_key=doomed.session_key
        RETURNING d.session_key
    )
    SELECT count(*) INTO removed FROM gone;
    RETURN removed;
END $$;
ALTER FUNCTION public.stewardship_anonymous_session_purge_v1(integer) SECURITY DEFINER;
REVOKE ALL ON FUNCTION public.stewardship_anonymous_session_purge_v1(integer) FROM PUBLIC;

DO $check$
BEGIN
    IF to_regprocedure('public.stewardship_family_session_purge_v1(uuid[])') IS NULL
       OR to_regprocedure('public.stewardship_anonymous_session_purge_v1(integer)') IS NULL THEN
        RAISE EXCEPTION 'Migration 0199 (Family session purge) is not installed as declared';
    END IF;
    -- Volatile plpgsql definers with the pinned search_path, the caller
    -- check and their deletes; CREATE OR REPLACE would drop SECURITY
    -- DEFINER, so prosecdef and proconfig are checked here.
    IF (SELECT count(*) FROM pg_proc p
        WHERE p.oid IN ('public.stewardship_family_session_purge_v1(uuid[])'::regprocedure,
                        'public.stewardship_anonymous_session_purge_v1(integer)'::regprocedure)
          AND p.prosecdef AND p.provolatile='v' AND NOT p.proretset
          AND p.prolang=(SELECT oid FROM pg_language WHERE lanname='plpgsql')
          AND p.proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
          AND p.prosrc LIKE '%IF session_user<>''pk_stewardship_worker'' AND NOT pg_has_role(session_user,%'
          AND p.prosrc LIKE '%DELETE FROM public.django_session d USING %')<>2
       OR NOT EXISTS (SELECT 1 FROM pg_proc p
           WHERE p.oid='public.stewardship_family_session_purge_v1(uuid[])'::regprocedure
             AND p.prosrc LIKE '%SET state=''cancelled'', ended_at=statement_timestamp(), version=b.version+1%'
             AND p.prosrc LIKE '%DELETE FROM public.stewardship_source_pin p USING cancelled%'
             AND p.prosrc LIKE '%DELETE FROM public.stewardship_family_session f WHERE f.id=ANY(ended)%'
             AND p.prosrc LIKE '%AND NOT (f.mode=''testing'' AND EXISTS (%')
       OR NOT EXISTS (SELECT 1 FROM pg_proc p
           WHERE p.oid='public.stewardship_anonymous_session_purge_v1(integer)'::regprocedure
             AND p.prosrc LIKE '%IF batch IS NULL OR batch<1 OR batch>1000 THEN%'
             AND p.prosrc LIKE '%FOR UPDATE OF d SKIP LOCKED%') THEN
        RAISE EXCEPTION 'Migration 0199 (Family session purge) is not installed as declared';
    END IF;
    -- Owned as the Admin session purge is (so they run with no other
    -- authority), with no EXECUTE for PUBLIC and no grant at all yet:
    -- database-grants adds the general worker's afterwards.
    IF EXISTS (SELECT 1 FROM pg_proc p
           WHERE p.oid IN ('public.stewardship_family_session_purge_v1(uuid[])'::regprocedure,
                           'public.stewardship_anonymous_session_purge_v1(integer)'::regprocedure)
             AND (p.proowner IS DISTINCT FROM (SELECT a.proowner FROM pg_proc a
                     WHERE a.oid='public.stewardship_admin_session_purge_v1(uuid[])'::regprocedure)
                  OR has_function_privilege('public', p.oid, 'EXECUTE')
                  OR EXISTS (SELECT 1 FROM aclexplode(p.proacl) a WHERE a.grantee<>p.proowner))) THEN
        RAISE EXCEPTION 'Migration 0199 (Family session purge) has unexpected owner or grants';
    END IF;
END
$check$;
