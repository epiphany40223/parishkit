-- Frozen forward migration file 0097 (the repository-wide file sequence):
-- the setup wizard's credential steps accept a sign-in up to an hour old
-- (#1402). This file is installed by the Django migration that names it in
-- FROZEN_SQL and must never change once released;
-- tests/stewardship/test_schema_migration_files.py pins its digest.
--
-- stewardship_setup_secret_guard_v1 admits a staged setup credential only
-- for the setup attempt's original owner and original live session, while
-- the attempt is collecting, in Testing mode with no campaign, over a
-- bootstrap configuration. It also required that session's Google sign-in
-- to be under five minutes old. Since #1353 the ParishSoft connection is
-- step 9, so an Administrator was asked to confirm with Google on almost
-- every credential page. The web now accepts a sign-in up to an hour old
-- on those steps (sessions.SETUP_FRESH_SECONDS), and this guard enforces
-- the same 60 minutes, the length of the Admin idle timeout.
--
-- This file replaces the guard with 0055's body (the latest; the baseline
-- carries the same text) changing only that interval, with a comment. It
-- stays SECURITY INVOKER with its pinned search_path and keeps its grants.
-- Every other fresh-sign-in rule, including secret replacement after setup
-- (five minutes), is unchanged, and so is every audit trigger.
--
-- The file ends with a DO block that refuses to commit unless all of this
-- is installed. Reversing needs its own forward migration.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

-- The function's grants before the replacement, compared in the DO block.
-- A transaction-local setting holds them, not a temporary table: the
-- migration login has no TEMP privilege.
SELECT set_config('stewardship.migration_0097_acl', (
    SELECT coalesce(p.proacl::text,'default')
    FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
    WHERE n.nspname='public' AND p.proname='stewardship_setup_secret_guard_v1'), true);

CREATE OR REPLACE FUNCTION public.stewardship_setup_secret_guard_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $_$
DECLARE attempt public.stewardship_setup_attempt%ROWTYPE;
        stamp timestamptz:=clock_timestamp(); keys text[]; item jsonb;
BEGIN
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'Setup credential tombstones are retained'
            USING ERRCODE='23514';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
        AND mode='ExclusiveLock' AND granted) THEN
        RAISE EXCEPTION 'Setup credentials require ordered ownership'
            USING ERRCODE='23514';
    END IF;
    SELECT * INTO attempt FROM public.stewardship_setup_attempt
        WHERE id=NEW.attempt_id;
    IF TG_OP='UPDATE' AND OLD.scrubbed_at IS NOT NULL THEN
        RAISE EXCEPTION 'Scrubbed setup credentials cannot be repopulated'
            USING ERRCODE='23514';
    END IF;
    IF TG_OP='UPDATE' AND NEW.scrubbed_at IS NOT NULL THEN
        IF attempt.state NOT IN ('expired','completed')
           OR NEW.ciphertext IS NOT NULL OR NEW.actor_id IS NOT NULL
           OR NEW.settings<>'{}'::jsonb
           OR NEW.fingerprint IS DISTINCT FROM OLD.fingerprint THEN
            RAISE EXCEPTION 'Setup credential scrub requires its terminal fence'
                USING ERRCODE='23514';
        END IF;
        NEW.scrubbed_at=stamp;
        RETURN NEW;
    END IF;
    IF current_user='pk_stewardship_scheduler' OR NEW.scrubbed_at IS NOT NULL
       OR attempt.state IS DISTINCT FROM 'collecting'
       OR NEW.actor_id IS DISTINCT FROM attempt.owner_id
       OR NOT EXISTS (
        SELECT 1 FROM public.stewardship_system_configuration runtime
        JOIN public.stewardship_configuration_version base
            ON base.id=runtime.active_configuration_id
            AND base.validation_schema IN ('bootstrap-policy-v1', 'bootstrap-users-v10')
        JOIN public.stewardship_portal_session login ON login.id=attempt.session_id
            AND login.principal_id=attempt.owner_id AND login.revoked_at IS NULL
            AND login.expires_at>stamp
            AND login.last_activity_at>stamp-interval '60 minutes'
            -- Setup's credential steps accept a sign-in up to an hour old (#1402).
            AND login.authenticated_at>stamp-interval '60 minutes'
        JOIN public.stewardship_portal_user owner ON owner.id=attempt.owner_id
            AND NOT owner.disabled
        JOIN public.stewardship_address_rule rule ON rule.email=owner.email
            AND rule.configuration_id=runtime.active_configuration_id
            AND rule.roles @> '["administrator"]'::jsonb
        WHERE runtime.mode='testing' AND NOT runtime.restore_review_required
            AND runtime.current_campaign_id IS NULL
            AND runtime.active_configuration_id=attempt.base_id) THEN
        RAISE EXCEPTION 'Setup credential requires its original freshly signed-in Admin'
            USING ERRCODE='23514';
    END IF;
    IF NEW.ciphertext IS NULL OR length(NEW.ciphertext)=0
       OR octet_length(NEW.ciphertext)>2097152
       OR jsonb_typeof(NEW.settings) IS DISTINCT FROM 'object'
       OR octet_length(NEW.settings::text)>2048 THEN
        RAISE EXCEPTION 'Invalid sealed setup credential shape' USING ERRCODE='23514';
    END IF;
    SELECT array_agg(key ORDER BY key) INTO keys
        FROM jsonb_object_keys(NEW.settings) key;
    IF NEW.target='parishsoft' THEN
        IF keys IS DISTINCT FROM ARRAY['organization_id']
           OR jsonb_typeof(NEW.settings->'organization_id') <> 'number'
           OR (NEW.settings->>'organization_id') !~ '^[1-9][0-9]{0,9}$'
           OR (NEW.settings->>'organization_id')::numeric >= 2147483648 THEN
            RAISE EXCEPTION 'Invalid provider validation context' USING ERRCODE='23514';
        END IF;
    ELSIF NEW.target='slack' THEN
        IF keys IS DISTINCT FROM ARRAY['channel_id']
           OR jsonb_typeof(NEW.settings->'channel_id') <> 'string'
           OR (NEW.settings->>'channel_id') !~ '^[CG][A-Z0-9]{1,63}$' THEN
            RAISE EXCEPTION 'Invalid provider validation context' USING ERRCODE='23514';
        END IF;
    ELSIF NEW.target='google_workspace' THEN
        IF keys IS DISTINCT FROM
            ARRAY['delegated_email','recipient','reply_to','sender'] THEN
            RAISE EXCEPTION 'Invalid provider validation context' USING ERRCODE='23514';
        END IF;
        FOR item IN SELECT value FROM jsonb_each(NEW.settings) LOOP
            IF jsonb_typeof(item) <> 'string' OR length(item#>>'{}')>254
               OR (item#>>'{}') <> lower(item#>>'{}')
               OR (item#>>'{}') ~ '[[:space:][:cntrl:]]'
               OR (item#>>'{}') !~ '^[^@]+@[^@]+$' THEN
                RAISE EXCEPTION 'Invalid provider validation context'
                    USING ERRCODE='23514';
            END IF;
        END LOOP;
    ELSE
        RAISE EXCEPTION 'Invalid provider validation context' USING ERRCODE='23514';
    END IF;

    RETURN NEW;
END $_$;

DO $check$
BEGIN
    -- The guard is still a trigger function running as its caller with its
    -- pinned search_path; it accepts a 60-minute sign-in, keeps the 60-minute
    -- idle rule and the original-owner, Testing and bootstrap conditions,
    -- and no longer has the five-minute sign-in rule.
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname='stewardship_setup_secret_guard_v1'
          AND pg_get_function_identity_arguments(p.oid)=''
          AND NOT p.prosecdef AND p.prorettype='trigger'::regtype
          AND p.proconfig @> ARRAY['search_path=pg_catalog, public, pg_temp']
          AND p.prosrc LIKE '%AND login.authenticated_at>stamp-interval ''60 minutes''%'
          AND p.prosrc LIKE '%AND login.last_activity_at>stamp-interval ''60 minutes''%'
          AND p.prosrc LIKE '%NEW.actor_id IS DISTINCT FROM attempt.owner_id%'
          AND p.prosrc LIKE '%WHERE runtime.mode=''testing'' AND NOT runtime.restore_review_required%'
          AND p.prosrc LIKE '%base.validation_schema IN (''bootstrap-policy-v1'', ''bootstrap-users-v10'')%'
          AND p.prosrc NOT LIKE '%authenticated_at>stamp-interval ''5 minutes''%')<>1
       OR (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
           WHERE n.nspname='public' AND p.proname='stewardship_setup_secret_guard_v1')<>1 THEN
        RAISE EXCEPTION 'Migration 0097: the setup credential guard is not installed as declared';
    END IF;
    IF current_setting('stewardship.migration_0097_acl', true) IS DISTINCT FROM (
           SELECT coalesce(p.proacl::text,'default')
           FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
           WHERE n.nspname='public' AND p.proname='stewardship_setup_secret_guard_v1') THEN
        RAISE EXCEPTION 'Migration 0097: the setup credential guard lost or changed its grants';
    END IF;
    -- The guard still guards the sealed setup credential table.
    IF NOT EXISTS (SELECT 1 FROM pg_trigger t JOIN pg_proc p ON p.oid=t.tgfoid
                   WHERE t.tgrelid='public.stewardship_setup_sealed_credential'::regclass
                     AND t.tgname='stewardship_setup_secret_admission'
                     AND p.proname='stewardship_setup_secret_guard_v1'
                     AND t.tgenabled='O') THEN
        RAISE EXCEPTION 'Migration 0097: the setup credential guard trigger is missing';
    END IF;
END
$check$;
