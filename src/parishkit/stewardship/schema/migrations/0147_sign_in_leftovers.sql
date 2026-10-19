-- Frozen forward migration file 0147 (the repository-wide file sequence):
-- schema left unused once the Admin portal stopped asking for a recent
-- sign-in (#1547, migration 0114) is removed (#1550). This file is installed
-- by the Django migration that names it in FROZEN_SQL and must never change
-- once released; tests/stewardship/test_schema_migration_files.py pins its
-- digest and checks that its copy of the portal session guard equals the
-- fresh-install baseline's (functions.sql).
--
-- It does two things, and changes no row:
--   - drops stewardship_automation_fresh_principal_v1 (0013's definer check,
--     migration-owned). Since 0114 no function calls it: the secret request
--     guards call stewardship_automation_instant_ended_v1 instead. Its
--     EXECUTE grants (web and the three key-replacement installers) go with
--     it. IF EXISTS lets the file run again where the function is already
--     gone (the users swap's test reruns every migration after the swap);
--     RESTRICT, the default, still fails rather than drop anything that
--     depends on it;
--   - makes an Admin session's verified sign-in instant (authenticated_at on
--     stewardship_portal_session) immutable. The session guard let it
--     advance in place for the retired step-up sign-in; a new sign-in now
--     issues a new session, so nothing moves it. The insert guard still
--     refuses a future instant.
-- No session, sign-in code or emailed link is touched: live Admin sessions
-- and every Family credential stay valid. The recorded sign-in columns, the
-- guards' "requires fresh ... authentication" messages (which Python
-- matches) and the readable admin_step_up event type stay.
--
-- The guard keeps its language, invoker rights, (empty) configuration and
-- grants: CREATE OR REPLACE keeps the grants, and the header repeats the
-- rest. The DO block at the end refuses to commit unless the function is
-- gone with nothing left calling it, the guard is installed exactly as
-- declared (its md5, and the key clauses spelled out for a reader) with its
-- attributes unchanged and its trigger enabled, and the checks 0114 kept
-- are still in place. Reversing needs its own forward migration.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

-- The guard's grants, rights and configuration before the replacement,
-- compared in the DO block (a transaction-local setting: the migration login
-- has no TEMP privilege).
SELECT set_config('stewardship.migration_0147_attributes', (
    SELECT string_agg(coalesce(p.proacl::text,'default')||'|'||p.prosecdef::text||'|'||coalesce(array_to_string(p.proconfig,','),'')||'|'||p.prolang::text, ';')
    FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
    WHERE n.nspname='public' AND p.proname='stewardship_portal_session_mutable_v1'), true);

DROP FUNCTION IF EXISTS public.stewardship_automation_fresh_principal_v1(uuid, timestamptz);

-- stewardship_portal_session_mutable_v1, from functions.sql.
CREATE OR REPLACE FUNCTION public.stewardship_portal_session_mutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                -- The verified Google instant is fixed when the session is
                -- issued: a new sign-in issues a new session (#1547), so
                -- nothing advances it in place any more (#1550).
                IF NEW."id" IS DISTINCT FROM OLD."id" OR NEW."created_at" IS DISTINCT FROM OLD."created_at" OR NEW."principal_id" IS DISTINCT FROM OLD."principal_id" OR NEW."session_id" IS DISTINCT FROM OLD."session_id" OR NEW."expires_at" IS DISTINCT FROM OLD."expires_at" OR NEW."authenticated_at" IS DISTINCT FROM OLD."authenticated_at" OR (OLD."revoked_at" IS NOT NULL AND NEW."revoked_at" IS DISTINCT FROM OLD."revoked_at") THEN
                    RAISE EXCEPTION 'Record identity and bindings are immutable'
                        USING ERRCODE = '23514';
                END IF;
                -- #306 M2: activity renews the idle limit only up to this
                -- statement's clock, never to a future instant.
                IF NEW."last_activity_at" IS DISTINCT FROM OLD."last_activity_at"
                   AND NEW."last_activity_at" > statement_timestamp() THEN
                    RAISE EXCEPTION 'Session activity cannot be recorded in the future'
                        USING ERRCODE = '23514';
                END IF;
                IF NEW.version IS DISTINCT FROM OLD.version + 1 THEN
                    RAISE EXCEPTION 'Every update must advance the record version'
                        USING ERRCODE = '23514';
                END IF;
                NEW.updated_at := statement_timestamp();
                RETURN NEW;
            END;
            $$;

DO $check$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname='stewardship_automation_fresh_principal_v1') THEN
        RAISE EXCEPTION 'Migration 0147: stewardship_automation_fresh_principal_v1 is still installed';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.prosrc LIKE '%stewardship_automation_fresh_principal%') THEN
        RAISE EXCEPTION 'Migration 0147: a function still calls stewardship_automation_fresh_principal_v1';
    END IF;
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname='stewardship_portal_session_mutable_v1'
          AND md5(p.prosrc)='9f596c034df2c94542257fa44991cbc0'
          AND p.prosrc LIKE '%#1550%'
          AND p.prosrc LIKE '%OR NEW."authenticated_at" IS DISTINCT FROM OLD."authenticated_at" OR (OLD."revoked_at" IS NOT NULL%'
          AND p.prosrc LIKE '%Record identity and bindings are immutable%'
          AND p.prosrc LIKE '%Session activity cannot be recorded in the future%'
          AND p.prosrc LIKE '%Every update must advance the record version%'
          AND p.prosrc NOT LIKE '%may only advance%')<>1 THEN
        RAISE EXCEPTION 'Migration 0147: stewardship_portal_session_mutable_v1 is not installed as declared';
    END IF;
    -- Rights, search_path, language and grants are as before.
    IF current_setting('stewardship.migration_0147_attributes', true) IS DISTINCT FROM (
           SELECT string_agg(coalesce(p.proacl::text,'default')||'|'||p.prosecdef::text||'|'||coalesce(array_to_string(p.proconfig,','),'')||'|'||p.prolang::text, ';')
           FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
           WHERE n.nspname='public' AND p.proname='stewardship_portal_session_mutable_v1') THEN
        RAISE EXCEPTION 'Migration 0147: the portal session guard changed its rights, configuration or grants';
    END IF;
    -- The guard still runs before every update of an Admin session.
    IF NOT EXISTS (SELECT 1 FROM pg_trigger t JOIN pg_proc p ON p.oid=t.tgfoid
        JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname='stewardship_portal_session_mutable_v1'
          AND t.tgrelid='public.stewardship_portal_session'::regclass
          AND t.tgenabled<>'D' AND NOT t.tgisinternal) THEN
        RAISE EXCEPTION 'Migration 0147: the portal session guard no longer has its trigger';
    END IF;
    -- What 0114 kept in place of the dropped check is still there: the
    -- ended-automation check (definer rights, no PUBLIC EXECUTE) and the
    -- command-session check.
    IF NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname='stewardship_automation_instant_ended_v1'
          AND p.prosecdef AND NOT has_function_privilege('public', p.oid, 'EXECUTE'))
       OR NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname='stewardship_automation_fresh_v1') THEN
        RAISE EXCEPTION 'Migration 0147: an automation session check 0114 kept is missing';
    END IF;
END
$check$;
