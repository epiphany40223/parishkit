-- Frozen forward migration file 0152 (the repository-wide file sequence): a
-- secret request made through an automation session names that session
-- instead of relying on its sign-in instant (#1763). This file is installed
-- by the Django migration that names it in FROZEN_SQL and must never change
-- once released; tests/stewardship/test_schema_migration_files.py pins its
-- digest and checks that its copies of baseline functions equal the
-- fresh-install baseline's (functions.sql).
--
-- Since 0114 (#1547) the intake and staged-to-testing guards refused a
-- request whose recorded sign-in instant a full-scope automation session of
-- the requester holds, once every such session had ended
-- (stewardship_automation_instant_ended_v1). An automation session records
-- its approving browser's sign-in instant, so that also refused the
-- browser's own key replacements until the Administrator signed in again.
-- The instant alone cannot tell the two apart, so the request now records
-- the session:
--   - stewardship_secret_request gains automation_session_id uuid NULL: the
--     automation session a command-line request was made through, NULL for
--     a browser request and for every existing row (a soft reference like
--     requested_by_id, with no foreign key; automation sessions are never
--     deleted). The web login's and the installers' table-level grants
--     already cover it; no grant changes. IF NOT EXISTS lets the file run
--     again (the users swap's tests rerun it);
--   - the new definer check stewardship_automation_request_live_v1(automation,
--     principal, instant) is true only when that session is live
--     (stewardship_automation_live_v1), of full scope, the principal's and
--     holds that sign-in instant; false, never NULL, otherwise. Definer
--     rights, because the credential installers read no automation table.
--     EXECUTE comes from the database-grants step (runtime_functions: web
--     and the parishsoft, google_workspace and slack installers, the same
--     logins as the check it replaces), never from PUBLIC;
--   - stewardship_sealed_intake_admission_v1 (0124's body) refuses a request
--     that names a session unless that check passes, and refuses any login
--     but the web or the schema owner naming one. A request that names none
--     gets no automation check;
--   - stewardship_secret_state_v2 (0114's body) lets a request that names a
--     session move from staged to testing only while that check passes (only
--     the three targets above; any other target with a named session is
--     refused), so ending the session still stops a credential it staged. A
--     request that names none gets no automation check. That includes a
--     setup install's request, which names no session, so 0114's skip for a
--     live setup install (stewardship_setup_install_live_v1) is no longer
--     needed and goes;
--   - stewardship_secret_request_mutable_v1 (the baseline's) keeps the
--     column fixed after intake, like the request's other bindings;
--   - stewardship_automation_instant_ended_v1 is dropped with its grants;
--     no function calls it any more. IF EXISTS lets the file run again;
--     RESTRICT, the default, still fails rather than drop a dependant.
-- A request staged before this file runs names no session, so it gets no
-- automation check at its staged-to-testing step. A staged request expires
-- within 24 hours, and the installer moves it on within moments, so only a
-- command-line key replacement in flight while the release is deployed is
-- affected. No row changes: no Admin session, Family session, emailed link
-- or code is touched. Error messages keep their wording, which callers
-- match.
--
-- CREATE OR REPLACE keeps each replaced function's owner and grants; every
-- header repeats its language and configuration. All three replaced
-- functions keep invoker rights; none is made SECURITY DEFINER by a baseline
-- ALTER. The DO block at the end refuses to commit unless the column, the
-- new check (definer rights, its body, its search_path, no PUBLIC EXECUTE)
-- and every replaced body are installed exactly as declared (its md5, and
-- the key clauses spelled out for a reader), with each replaced function's
-- owner, rights, configuration, grants and enabled triggers unchanged, and
-- the dropped check gone with nothing calling it. Reversing needs its own
-- forward migration.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

-- Each replaced function's owner, grants, rights and configuration, and how
-- many enabled triggers it serves (the sealed intake check guards two
-- tables), before the replacement, compared in the DO block (transaction-
-- local settings: the migration login has no TEMP privilege).
SELECT set_config('stewardship.migration_0152_attributes', (
    SELECT string_agg(p.proname||'='||coalesce(p.proacl::text,'default')||'|'||p.prosecdef::text||'|'||coalesce(array_to_string(p.proconfig,','),'')||'|'||p.prolang::text||'|'||p.proowner::regrole::text, ';' ORDER BY p.proname)
    FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
    WHERE n.nspname='public' AND p.proname IN (
            'stewardship_sealed_intake_admission_v1',
            'stewardship_secret_request_mutable_v1',
            'stewardship_secret_state_v2'
    )), true);
SELECT set_config('stewardship.migration_0152_triggers', (
    SELECT string_agg(p.proname||'='||(SELECT count(*) FROM pg_trigger t WHERE t.tgfoid=p.oid AND t.tgenabled<>'D')::text, ';' ORDER BY p.proname)
    FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
    WHERE n.nspname='public' AND p.proname IN (
            'stewardship_sealed_intake_admission_v1',
            'stewardship_secret_request_mutable_v1',
            'stewardship_secret_state_v2'
    )), true);

ALTER TABLE public.stewardship_secret_request ADD COLUMN IF NOT EXISTS automation_session_id uuid NULL;

-- True only when `automation` is a live, full-scope automation session of
-- `principal` whose sign-in instant is `instant`; false (never NULL)
-- otherwise (#1763, ADM-11). A plain CREATE: migration-owned, like the check
-- it replaces.
CREATE FUNCTION public.stewardship_automation_request_live_v1(automation uuid, principal uuid, instant timestamptz) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT EXISTS (SELECT 1 FROM public.stewardship_automation_session a
            WHERE a.id=automation AND a.principal_id=principal AND a.scope='full'
              AND a.authenticated_at=instant
              AND public.stewardship_automation_live_v1(a.id))
$$;
REVOKE ALL ON FUNCTION public.stewardship_automation_request_live_v1(uuid, uuid, timestamptz) FROM PUBLIC;

-- stewardship_sealed_intake_admission_v1, from 0124_guard_wording.sql: a
-- named automation session in place of the ended-instant check.
CREATE OR REPLACE FUNCTION public.stewardship_sealed_intake_admission_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE live boolean := true;
BEGIN
    IF TG_TABLE_NAME='stewardship_secret_request' THEN
        -- A request made through an automation session names it (#1763):
        -- it must be a live, full-scope session of the requester holding
        -- the recorded sign-in instant (ADM-11), so an ended session
        -- stages nothing. A browser request names none and is not checked
        -- here, whatever automation sessions its sign-in approved; its
        -- sign-in may be of any age (#1547). Only the web (which holds
        -- EXECUTE on the check) or the schema owner may name a session;
        -- any other login naming one is refused. The call is its own
        -- statement: EXECUTE is checked whenever an expression is
        -- initialized.
        IF NEW.automation_session_id IS NOT NULL THEN
            IF current_user='pk_stewardship_web' OR pg_has_role(current_user,
                (SELECT nspowner FROM pg_namespace WHERE nspname='public'),'USAGE') THEN
                live := coalesce(public.stewardship_automation_request_live_v1(
                    NEW.automation_session_id,NEW.requested_by_id,NEW.reauthenticated_at),false);
            ELSE
                live := false;
            END IF;
        END IF;
        IF NOT live THEN
            RAISE EXCEPTION 'Sealed intake requires a live automation session'
                USING ERRCODE='23514';
        END IF;
    ELSE
        IF NOT EXISTS(SELECT 1 FROM stewardship_secret_request
            WHERE id=NEW.request_id AND required_consumers<>'[]'::jsonb) THEN
            RAISE EXCEPTION 'Sealed intake requires installer consumers'
                USING ERRCODE='23514';
        END IF;
    END IF;
    RETURN NEW;
END $$;

-- stewardship_secret_request_mutable_v1, from the baseline functions.sql:
-- the named automation session is fixed after intake too.
CREATE OR REPLACE FUNCTION public.stewardship_secret_request_mutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                IF NEW."id" IS DISTINCT FROM OLD."id" OR NEW."created_at" IS DISTINCT FROM OLD."created_at" OR NEW."target" IS DISTINCT FROM OLD."target" OR NEW."staging_reference" IS DISTINCT FROM OLD."staging_reference" OR NEW."requested_by_id" IS DISTINCT FROM OLD."requested_by_id" OR NEW."reauthenticated_at" IS DISTINCT FROM OLD."reauthenticated_at" OR NEW."expires_at" IS DISTINCT FROM OLD."expires_at" OR NEW."expected_fingerprint" IS DISTINCT FROM OLD."expected_fingerprint" OR NEW."required_consumers" IS DISTINCT FROM OLD."required_consumers" OR NEW."automation_session_id" IS DISTINCT FROM OLD."automation_session_id" OR (OLD."resulting_fingerprint" IS NOT NULL AND NEW."resulting_fingerprint" IS DISTINCT FROM OLD."resulting_fingerprint") OR (OLD."installed_at" IS NOT NULL AND NEW."installed_at" IS DISTINCT FROM OLD."installed_at") OR (OLD."acknowledged_at" IS NOT NULL AND NEW."acknowledged_at" IS DISTINCT FROM OLD."acknowledged_at") THEN
                    RAISE EXCEPTION 'Record identity and bindings are immutable'
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

-- stewardship_secret_state_v2, from 0114_no_sign_in_age.sql: a named
-- automation session in place of the ended-instant check at the
-- staged-to-testing step.
CREATE OR REPLACE FUNCTION public.stewardship_secret_state_v2() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE live boolean := true;
BEGIN
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'Secret request history cannot be deleted' USING ERRCODE='23514';
    END IF;
    IF jsonb_typeof(NEW.required_consumers)<>'array'
       OR jsonb_array_length(NEW.required_consumers)>6
       OR EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.required_consumers) x WHERE jsonb_typeof(x)<>'string')
       OR NOT stewardship_credential_consumers_v1(NEW.target) @> NEW.required_consumers
       OR (SELECT count(*)<>count(DISTINCT x) FROM jsonb_array_elements(NEW.required_consumers) x) THEN
        RAISE EXCEPTION 'Invalid credential consumer inventory' USING ERRCODE='23514';
    END IF;
    IF TG_OP='INSERT' THEN
        NEW.created_at:=statement_timestamp(); NEW.updated_at:=NEW.created_at;
        IF NEW.state<>'staged' OR NEW.version<>1 OR NEW.actor_id IS DISTINCT FROM NEW.requested_by_id
           OR NEW.expires_at<=statement_timestamp() OR NEW.resulting_fingerprint IS NOT NULL
           OR NEW.installed_at IS NOT NULL OR NEW.acknowledged_at IS NOT NULL THEN
            RAISE EXCEPTION 'Invalid secret request intake' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.required_consumers IS DISTINCT FROM OLD.required_consumers
       OR (OLD.resulting_fingerprint IS NOT NULL AND NEW.resulting_fingerprint IS DISTINCT FROM OLD.resulting_fingerprint)
       OR (OLD.installed_at IS NOT NULL AND NEW.installed_at IS DISTINCT FROM OLD.installed_at)
       OR (OLD.acknowledged_at IS NOT NULL AND NEW.acknowledged_at IS DISTINCT FROM OLD.acknowledged_at) THEN
        RAISE EXCEPTION 'Credential installation evidence is immutable' USING ERRCODE='23514';
    END IF;
    IF OLD.required_consumers<>'[]'::jsonb
       AND NOT(OLD.state='staged' AND NEW.state='cleanup_pending' AND NEW.cleanup_reason='cancelled')
       AND current_user<>'pk_stewardship_credential_'||OLD.target THEN
        RAISE EXCEPTION 'Only the target installer may advance this request' USING ERRCODE='23514';
    END IF;
    IF OLD.resulting_fingerprint IS NULL AND NEW.resulting_fingerprint IS NOT NULL
       AND NOT(OLD.state='testing' AND NEW.state='installing') THEN
        RAISE EXCEPTION 'Credential fingerprint requires successful testing' USING ERRCODE='23514';
    END IF;
    IF OLD.installed_at IS NULL AND NEW.installed_at IS NOT NULL
       AND NOT(OLD.state='installing' AND NEW.state='awaiting_ack') THEN
        RAISE EXCEPTION 'Credential install instant requires installation' USING ERRCODE='23514';
    END IF;
    IF OLD.acknowledged_at IS NULL AND NEW.acknowledged_at IS NOT NULL
       AND NOT(OLD.state='awaiting_ack' AND NEW.state='cleanup_pending' AND NEW.cleanup_reason='applied') THEN
        RAISE EXCEPTION 'Credential acknowledgement requires consumer evidence' USING ERRCODE='23514';
    END IF;
    IF OLD.state='staged' AND NEW.state='testing' THEN
        -- A request made through an automation session names it (#1763):
        -- that session must still be live, of full scope, the requester's
        -- and hold the recorded sign-in instant (ADM-11), so ending it
        -- stops the credential it staged here. A browser request names
        -- none and is not checked, whatever its sign-in approved; its
        -- sign-in may be of any age (#1547). Only these targets'
        -- installers hold EXECUTE on the check, so it is its own
        -- statement, reached only for them; no other target takes an
        -- automation session's request.
        IF NEW.automation_session_id IS NOT NULL THEN
            IF NEW.target IN ('parishsoft','google_workspace','slack') THEN
                live := coalesce(public.stewardship_automation_request_live_v1(
                    NEW.automation_session_id,NEW.requested_by_id,NEW.reauthenticated_at),false);
            ELSE
                live := false;
            END IF;
        END IF;
        IF NEW.actor_id IS NOT NULL OR NEW.expires_at<=statement_timestamp()
           OR NOT live
           OR NEW.required_consumers='[]'::jsonb
           OR NOT EXISTS(SELECT 1 FROM stewardship_sealed_credential_staging WHERE request_id=NEW.id AND ciphertext IS NOT NULL) THEN
            RAISE EXCEPTION 'Credential testing requires a live sealed request' USING ERRCODE='23514';
        END IF;
    ELSIF OLD.state='testing' AND NEW.state='installing' THEN
        IF NEW.resulting_fingerprint IS NULL OR NEW.expires_at<=statement_timestamp() OR NEW.actor_id IS NOT NULL THEN
            RAISE EXCEPTION 'Credential installation requires tested fingerprint' USING ERRCODE='23514';
        END IF;
        IF NOT EXISTS(SELECT 1 FROM stewardship_sealed_credential_staging
            WHERE request_id=NEW.id AND fingerprint=NEW.resulting_fingerprint AND ciphertext IS NOT NULL) THEN
            RAISE EXCEPTION 'Tested credential must match its sealed intake' USING ERRCODE='23514'; END IF;
    ELSIF OLD.state='installing' AND NEW.state='awaiting_ack' THEN
        IF NEW.actor_id IS NOT NULL THEN RAISE EXCEPTION 'Installation requires system attribution' USING ERRCODE='23514'; END IF;
        NEW.installed_at:=statement_timestamp();
    ELSIF OLD.state IN('staged','testing','installing','awaiting_ack') AND NEW.state='cleanup_pending' THEN
        IF NEW.cleanup_reason='cancelled' THEN
            IF NEW.actor_id IS DISTINCT FROM OLD.requested_by_id THEN
                RAISE EXCEPTION 'Invalid cancellation attribution' USING ERRCODE='23514'; END IF;
        ELSIF NEW.actor_id IS NOT NULL THEN
            RAISE EXCEPTION 'Cleanup requires system attribution' USING ERRCODE='23514';
        END IF;
        IF NEW.cleanup_reason='expired' AND OLD.expires_at>statement_timestamp() AND NOT (CASE WHEN NEW.target IN ('parishsoft','google_workspace','slack') THEN EXISTS (SELECT 1 FROM public.stewardship_setup_credential_install WHERE request_id=NEW.id) AND NOT public.stewardship_setup_install_live_v1(NEW.id) ELSE false END) THEN
            RAISE EXCEPTION 'Secret request is not expired' USING ERRCODE='23514';
        END IF;
        IF NEW.cleanup_reason='applied' THEN
            IF OLD.state<>'awaiting_ack' OR (OLD.expires_at<=statement_timestamp() AND CASE WHEN NEW.target IN ('parishsoft','google_workspace','slack') THEN public.stewardship_setup_install_completed_v1(NEW.id) IS NULL ELSE true END)
               OR EXISTS(SELECT 1 FROM jsonb_array_elements_text(NEW.required_consumers) c
                    WHERE NOT EXISTS(SELECT 1 FROM stewardship_credential_consumer_ack a
                        WHERE a.request_id=NEW.id AND a.consumer=c AND a.fingerprint=NEW.resulting_fingerprint)) THEN
                RAISE EXCEPTION 'Credential consumers have not acknowledged' USING ERRCODE='23514';
            END IF;
            NEW.acknowledged_at:=CASE WHEN NEW.target IN ('parishsoft','google_workspace','slack') THEN coalesce(public.stewardship_setup_install_completed_v1(NEW.id),statement_timestamp()) ELSE statement_timestamp() END;
        END IF;
    ELSIF OLD.state='cleanup_pending' AND NEW.state=OLD.cleanup_reason AND NEW.cleanup_reason=OLD.cleanup_reason THEN
        IF NEW.actor_id IS NOT NULL THEN RAISE EXCEPTION 'Cleanup requires system attribution' USING ERRCODE='23514'; END IF;
        IF EXISTS(SELECT 1 FROM stewardship_sealed_credential_staging WHERE request_id=NEW.id AND ciphertext IS NOT NULL) THEN
            RAISE EXCEPTION 'Credential ciphertext must be scrubbed before completion' USING ERRCODE='23514'; END IF;
        NEW.scrubbed_at:=statement_timestamp();
    ELSE
        RAISE EXCEPTION 'Invalid secret request transition' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;

DROP FUNCTION IF EXISTS public.stewardship_automation_instant_ended_v1(uuid, timestamptz);

DO $check$
BEGIN
    -- The column: a nullable uuid with no default, on the request table.
    IF NOT EXISTS (SELECT 1 FROM pg_attribute a
        WHERE a.attrelid='public.stewardship_secret_request'::regclass
          AND a.attname='automation_session_id' AND NOT a.attisdropped
          AND a.atttypid='uuid'::regtype AND NOT a.attnotnull AND NOT a.atthasdef) THEN
        RAISE EXCEPTION 'Migration 0152: stewardship_secret_request.automation_session_id is not installed as declared';
    END IF;
    -- The new check: definer rights, its search_path, its exact body and
    -- no PUBLIC EXECUTE.
    IF NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname='stewardship_automation_request_live_v1'
          AND p.prosecdef AND NOT p.proisstrict AND p.provolatile='s'
          AND p.prorettype='boolean'::regtype
          AND p.prolang=(SELECT oid FROM pg_language WHERE lanname='sql')
          AND p.proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
          AND pg_get_function_identity_arguments(p.oid)='automation uuid, principal uuid, instant timestamp with time zone'
          AND md5(p.prosrc)='8ac9a3082a63ab08eedcd325b823ef97'
          -- Only its owner holds any privilege yet: database-grants gives
          -- the runtime logins EXECUTE later.
          AND NOT EXISTS (SELECT 1 FROM aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) x
              WHERE x.grantee<>p.proowner)
          AND p.prosrc LIKE '%a.id=automation AND a.principal_id=principal AND a.scope=''full''%'
          AND p.prosrc LIKE '%a.authenticated_at=instant%'
          AND p.prosrc LIKE '%public.stewardship_automation_live_v1(a.id)%'
          AND NOT has_function_privilege('public', p.oid, 'EXECUTE')) THEN
        RAISE EXCEPTION 'Migration 0152: stewardship_automation_request_live_v1 is not installed as declared';
    END IF;
    -- Each replaced function is its one overload, with exactly the new body.
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname='stewardship_sealed_intake_admission_v1'
          AND md5(p.prosrc)='5a2b301b9895a4b974fa50d4911644c4'
          AND p.prosrc LIKE '%#1763%'
          AND p.prosrc LIKE '%IF NEW.automation_session_id IS NOT NULL THEN%'
          AND p.prosrc LIKE '%public.stewardship_automation_request_live_v1(
                    NEW.automation_session_id,NEW.requested_by_id,NEW.reauthenticated_at),false)%'
          AND p.prosrc LIKE '%live := false;%'
          AND p.prosrc LIKE '%IF NOT live THEN%'
          AND p.prosrc LIKE '%Sealed intake requires a live automation session%'
          AND p.prosrc LIKE '%Sealed intake requires installer consumers%'
          AND p.prosrc NOT LIKE '%instant_ended%')<>1 THEN
        RAISE EXCEPTION 'Migration 0152: stewardship_sealed_intake_admission_v1 is not installed as declared';
    END IF;
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname='stewardship_secret_state_v2'
          AND md5(p.prosrc)='a0ceac172ee012444271b82685c56eb8'
          AND p.prosrc LIKE '%#1763%'
          AND p.prosrc LIKE '%IF NEW.automation_session_id IS NOT NULL THEN
            IF NEW.target IN (''parishsoft'',''google_workspace'',''slack'') THEN%'
          AND p.prosrc LIKE '%public.stewardship_automation_request_live_v1(
                    NEW.automation_session_id,NEW.requested_by_id,NEW.reauthenticated_at),false)%'
          AND p.prosrc LIKE '%OR NOT live%'
          AND p.prosrc LIKE '%Credential testing requires a live sealed request%'
          AND p.prosrc LIKE '%Only the target installer may advance this request%'
          AND p.prosrc NOT LIKE '%instant_ended%')<>1 THEN
        RAISE EXCEPTION 'Migration 0152: stewardship_secret_state_v2 is not installed as declared';
    END IF;
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname='stewardship_secret_request_mutable_v1'
          AND md5(p.prosrc)='cca720623328f7855ece4b453d9d299f'
          AND p.prosrc LIKE '%OR NEW."automation_session_id" IS DISTINCT FROM OLD."automation_session_id" OR%'
          AND p.prosrc LIKE '%OR NEW."reauthenticated_at" IS DISTINCT FROM OLD."reauthenticated_at" OR%'
          AND p.prosrc LIKE '%Record identity and bindings are immutable%'
          AND p.prosrc LIKE '%Every update must advance the record version%')<>1 THEN
        RAISE EXCEPTION 'Migration 0152: stewardship_secret_request_mutable_v1 is not installed as declared';
    END IF;
    -- The ended-instant check is gone, and nothing calls it.
    IF EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname='stewardship_automation_instant_ended_v1') THEN
        RAISE EXCEPTION 'Migration 0152: stewardship_automation_instant_ended_v1 is still installed';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.prosrc LIKE '%stewardship_automation_instant_ended%') THEN
        RAISE EXCEPTION 'Migration 0152: a function still calls stewardship_automation_instant_ended_v1';
    END IF;
    -- Owner, rights, search_path, language and grants are as before.
    IF current_setting('stewardship.migration_0152_attributes', true) IS DISTINCT FROM (
           SELECT string_agg(p.proname||'='||coalesce(p.proacl::text,'default')||'|'||p.prosecdef::text||'|'||coalesce(array_to_string(p.proconfig,','),'')||'|'||p.prolang::text||'|'||p.proowner::regrole::text, ';' ORDER BY p.proname)
           FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
           WHERE n.nspname='public' AND p.proname IN (
               'stewardship_sealed_intake_admission_v1',
               'stewardship_secret_request_mutable_v1',
               'stewardship_secret_state_v2')) THEN
        RAISE EXCEPTION 'Migration 0152: a replaced function changed its rights, configuration or grants';
    END IF;
    -- None of them gained definer rights.
    IF EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.prosecdef AND p.proname IN (
            'stewardship_sealed_intake_admission_v1',
            'stewardship_secret_request_mutable_v1',
            'stewardship_secret_state_v2')) THEN
        RAISE EXCEPTION 'Migration 0152: a replaced guard has definer rights';
    END IF;
    -- Every one is a trigger function that still guards every table it
    -- guarded, with each of its triggers still enabled.
    IF EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname IN (
            'stewardship_sealed_intake_admission_v1',
            'stewardship_secret_request_mutable_v1',
            'stewardship_secret_state_v2')
          AND (p.prorettype<>'trigger'::regtype
               OR NOT EXISTS (SELECT 1 FROM pg_trigger t WHERE t.tgfoid=p.oid AND t.tgenabled<>'D')))
       OR current_setting('stewardship.migration_0152_triggers', true) IS DISTINCT FROM (
           SELECT string_agg(p.proname||'='||(SELECT count(*) FROM pg_trigger t WHERE t.tgfoid=p.oid AND t.tgenabled<>'D')::text, ';' ORDER BY p.proname)
           FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
           WHERE n.nspname='public' AND p.proname IN (
               'stewardship_sealed_intake_admission_v1',
               'stewardship_secret_request_mutable_v1',
               'stewardship_secret_state_v2')) THEN
        RAISE EXCEPTION 'Migration 0152: a replaced guard no longer has its trigger';
    END IF;
END
$check$;
