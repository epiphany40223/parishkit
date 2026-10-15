-- Frozen forward migration file 0088 (the repository-wide file sequence):
-- Allow debug logging again, System health's Testing-only clear of the
-- debug-off switch (ADM-13 PR 6c, #1207). This file is installed by its
-- Django migration in the jobs app and must never change once released;
-- tests/stewardship/test_schema_migration_files.py pins its digest.
-- Reversing needs its own forward migration.
--
-- 0078 created the switch and its guard, which refuses every clear except
-- the schema owner's (the server operator's offline definer). System
-- health decision 3 (2026-10-04) also lets an Administrator clear it from
-- the page, in Testing mode only. This replaces the guard
-- (stewardship_debug_off_switch_guard_v1, migration-owned: 0078 created it,
-- so no baseline copy exists) with 0078's body plus that one admitted
-- update. Inserts, deletes, the owner's clear and every refusal 0078 makes
-- are unchanged. A non-owner UPDATE is now admitted only when all of these
-- hold:
--   - the login is the web login (anything else is still "Only the server
--     operator clears the debug-off switch");
--   - the global mode is Testing (in Production the same words, naming
--     Production: only the offline operator clears it there);
--   - who set the switch and when are unchanged, and it is cleared once, as
--     for the owner;
--   - the update names an Administrator's clear (cleared_by
--     'administrator', cleared_actor_id set);
--   - that Administrator is enabled and has a live session that signed in
--     within the last five minutes, or a live full-scope automation
--     session (stewardship_automation_fresh_v1), as the insert requires.
--     The row has no column for the clearing session, so any such session
--     of the named Administrator admits it; the web login names the actor
--     either way, as it names the session on insert.
-- The clear time is the database's, as for the owner. The mode is read at
-- the statement's snapshot (READ COMMITTED, which the guard already
-- requires of a non-owner); a switch to Production commits only after
-- go-live's own checks, and the clear it races with is a Testing action.
--
-- The web login's column UPDATE grant on (cleared_at, cleared_by,
-- cleared_actor_id) is declared in runtime_grants and applied by
-- database-grants; before this file is applied the old guard refuses it.
--
-- CREATE OR REPLACE keeps the function's owner, ACL and trigger; the
-- REVOKE below only restates that PUBLIC has no EXECUTE. The DO block at the
-- end refuses to commit unless the new body (and still every part of 0078's
-- that 0078's own check names) is installed as declared. No temporary
-- objects: the migration login has no TEMP privilege.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

CREATE OR REPLACE FUNCTION public.stewardship_debug_off_switch_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE
    owner boolean := pg_has_role(current_user,
        (SELECT nspowner FROM pg_namespace WHERE nspname='public'),'USAGE');
    instant timestamptz := statement_timestamp();
BEGIN
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'The debug-off switch is never deleted' USING ERRCODE='42501';
    END IF;
    -- The insert's check reads other rows at the statement's own snapshot,
    -- which only READ COMMITTED gives a statement that waited on the lock.
    IF NOT owner AND current_setting('transaction_isolation')<>'read committed' THEN
        RAISE EXCEPTION 'A debug-off switch change requires READ COMMITTED' USING ERRCODE='42501';
    END IF;
    IF TG_OP='INSERT' THEN
        IF owner THEN
            RETURN NEW;
        END IF;
        IF current_user IS DISTINCT FROM 'pk_stewardship_web' THEN
            RAISE EXCEPTION 'Only the web portal sets the debug-off switch' USING ERRCODE='42501';
        END IF;
        -- One set switch at a time: serialize inserts before looking.
        PERFORM pg_advisory_xact_lock(736241, 3);
        IF NEW.actor_id IS NULL
           OR public.stewardship_export_authorized_v1(NEW.actor_id,true) IS NOT TRUE
           OR NOT EXISTS(SELECT 1 FROM public.stewardship_portal_session login
                WHERE login.id=NEW.session_id AND login.principal_id=NEW.actor_id
                  AND login.revoked_at IS NULL AND login.expires_at>clock_timestamp()
                  AND login.last_activity_at>clock_timestamp()-interval '60 minutes'
                  AND login.authenticated_at=NEW.authenticated_at
                  AND (login.authenticated_at BETWEEN clock_timestamp()-interval '5 minutes'
                           AND clock_timestamp()
                       -- A live full-scope automation session stands in for a
                       -- fresh sign-in, as in the other fresh-gated guards.
                       OR public.stewardship_automation_fresh_v1(login.id,NEW.actor_id))) THEN
            RAISE EXCEPTION 'The debug-off switch requires fresh Administrator authentication' USING ERRCODE='42501';
        END IF;
        IF EXISTS(SELECT 1 FROM public.stewardship_debug_off_switch s
                  WHERE s.cleared_at IS NULL) THEN
            RAISE EXCEPTION 'The debug-off switch is already set' USING ERRCODE='23505';
        END IF;
        NEW.set_at:=instant;
        NEW.cleared_at:=NULL;
        NEW.cleared_by:=NULL;
        NEW.cleared_actor_id:=NULL;
        RETURN NEW;
    END IF;
    -- A non-owner clear is the web login's Allow debug logging again, and
    -- only in Testing mode (System health decision 3).
    IF NOT owner AND current_user IS DISTINCT FROM 'pk_stewardship_web' THEN
        RAISE EXCEPTION 'Only the server operator clears the debug-off switch' USING ERRCODE='42501';
    END IF;
    IF NOT owner AND NOT EXISTS(SELECT 1 FROM public.stewardship_system_configuration r
                                WHERE r.mode='testing') THEN
        RAISE EXCEPTION 'Only the server operator clears the debug-off switch in Production' USING ERRCODE='42501';
    END IF;
    IF NEW.id IS DISTINCT FROM OLD.id OR NEW.set_at IS DISTINCT FROM OLD.set_at
       OR NEW.actor_id IS DISTINCT FROM OLD.actor_id
       OR NEW.session_id IS DISTINCT FROM OLD.session_id
       OR NEW.authenticated_at IS DISTINCT FROM OLD.authenticated_at THEN
        RAISE EXCEPTION 'A debug-off switch keeps who set it and when' USING ERRCODE='23514';
    END IF;
    IF OLD.cleared_at IS NOT NULL OR NEW.cleared_at IS NULL THEN
        RAISE EXCEPTION 'A debug-off switch is cleared once and stays cleared' USING ERRCODE='23514';
    END IF;
    IF NOT owner THEN
        IF NEW.cleared_by IS DISTINCT FROM 'administrator' OR NEW.cleared_actor_id IS NULL THEN
            RAISE EXCEPTION 'The web clears the debug-off switch only as an Administrator' USING ERRCODE='23514';
        END IF;
        IF public.stewardship_export_authorized_v1(NEW.cleared_actor_id,true) IS NOT TRUE
           OR NOT EXISTS(SELECT 1 FROM public.stewardship_portal_session login
                WHERE login.principal_id=NEW.cleared_actor_id
                  AND login.revoked_at IS NULL AND login.expires_at>clock_timestamp()
                  AND login.last_activity_at>clock_timestamp()-interval '60 minutes'
                  AND (login.authenticated_at BETWEEN clock_timestamp()-interval '5 minutes'
                           AND clock_timestamp()
                       OR public.stewardship_automation_fresh_v1(login.id,NEW.cleared_actor_id))) THEN
            RAISE EXCEPTION 'Allowing debug logging again requires fresh Administrator authentication' USING ERRCODE='42501';
        END IF;
    END IF;
    NEW.cleared_at:=instant;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.stewardship_debug_off_switch_guard_v1() FROM PUBLIC;

-- Refuse to commit unless the guard is this file's (the Testing-only clear
-- for the web login only, keeping who set the switch and when, cleared once
-- at the database's time, with 0078's checks kept), still invoker's rights
-- with the fixed search_path and no PUBLIC EXECUTE, and still the switch's
-- trigger function. The missing function is checked first, on its own, because
-- PostgreSQL does not short-circuit OR and the ::regprocedure casts below
-- would otherwise fail with their own error instead of this file's.
DO $check$
BEGIN
    IF to_regprocedure('public.stewardship_debug_off_switch_guard_v1()') IS NULL
       OR to_regclass('public.stewardship_debug_off_switch') IS NULL THEN
        RAISE EXCEPTION 'Migration 0088 (allow debug logging again) is not installed as declared';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM pg_proc p
        WHERE p.oid='public.stewardship_debug_off_switch_guard_v1()'::regprocedure
          AND NOT p.prosecdef
          AND p.proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
          AND p.prosrc LIKE '%transaction_isolation%'
          AND p.prosrc LIKE '%pg_advisory_xact_lock(736241, 3)%'
          AND p.prosrc LIKE '%public.stewardship_automation_fresh_v1(login.id,NEW.actor_id)%'
          AND p.prosrc LIKE '%IF NOT owner AND current_user IS DISTINCT FROM ''pk_stewardship_web'' THEN%'
          AND p.prosrc LIKE '%Only the server operator clears the debug-off switch in Production%'
          AND p.prosrc LIKE '%NEW.authenticated_at IS DISTINCT FROM OLD.authenticated_at%'
          AND p.prosrc LIKE '%IF OLD.cleared_at IS NOT NULL OR NEW.cleared_at IS NULL THEN%'
          AND p.prosrc LIKE '%NEW.cleared_at:=instant;%'
          AND p.prosrc LIKE '%WHERE r.mode=''testing''%'
          AND p.prosrc LIKE '%NEW.cleared_by IS DISTINCT FROM ''administrator''%'
          AND p.prosrc LIKE '%public.stewardship_export_authorized_v1(NEW.cleared_actor_id,true)%'
          AND p.prosrc LIKE '%public.stewardship_automation_fresh_v1(login.id,NEW.cleared_actor_id)%')
       OR has_function_privilege('public',
            'public.stewardship_debug_off_switch_guard_v1()', 'EXECUTE')
       OR NOT EXISTS(SELECT 1 FROM pg_trigger t
        WHERE t.tgrelid='public.stewardship_debug_off_switch'::regclass
          AND t.tgname='stewardship_debug_off_switch_guard' AND t.tgenabled='O'
          AND t.tgfoid='public.stewardship_debug_off_switch_guard_v1()'::regprocedure
          AND t.tgtype=31) THEN
        RAISE EXCEPTION 'Migration 0088 (allow debug logging again) is not installed as declared';
    END IF;
END
$check$;
