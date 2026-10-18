-- Frozen forward migration file 0111 (the repository-wide file sequence):
-- turning Slack off during setup discards the staged Slack token at once
-- (#1499). This file is installed by the Django migration that names it in
-- FROZEN_SQL and must never change once released;
-- tests/stewardship/test_schema_migration_files.py pins its digest.
--
-- A setup attempt's staged credentials were scrubbed only when the attempt
-- expired or completed, so a Slack token the Administrator no longer wanted
-- (Slack turned off after a failed Test Slack) stayed sealed until then,
-- and turning Slack on again offered it as a saved credential. Now a save
-- that leaves the Slack step off scrubs the staged Slack token in the same
-- transaction, and turning Slack on again stages a new one.
--
-- This file:
--   replaces stewardship_setup_secret_guard_v1 (0104's body, the latest):
--     a scrub of a still-collecting attempt's credential is admitted only
--     from the web, for the Slack target, for the attempt's owner, while
--     the attempt's saved Slack step is off; every other scrub keeps the
--     terminal fence (expired or completed, by the system). It stays
--     SECURITY INVOKER with its pinned search_path and keeps its grants.
--   drops the constraint setup_secret_attempt_target (one row per attempt
--     and target, scrubbed or not) and creates the partial unique index
--     setup_secret_one_live_target (one unscrubbed row per attempt and
--     target), so the scrubbed row stays as the audit's tombstone beside
--     the newly staged one. Every reader of a staged credential already
--     selects it by id or with scrubbed_at IS NULL.
--
-- The file ends with a DO block that refuses to commit unless all of this
-- is installed. Reversing needs its own forward migration.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

-- The guard's grants before the replacement, compared in the DO block. A
-- transaction-local setting holds them, not a temporary table: the
-- migration login has no TEMP privilege.
SELECT set_config('stewardship.migration_0111_acl', (
    SELECT coalesce(p.proacl::text,'default')
    FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
    WHERE n.nspname='public' AND p.proname='stewardship_setup_secret_guard_v1'), true);

ALTER TABLE public.stewardship_setup_sealed_credential
    DROP CONSTRAINT IF EXISTS setup_secret_attempt_target;
CREATE UNIQUE INDEX IF NOT EXISTS setup_secret_one_live_target
    ON public.stewardship_setup_sealed_credential USING btree (attempt_id, target)
    WHERE (scrubbed_at IS NULL);

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
        IF NEW.ciphertext IS NOT NULL OR NEW.settings<>'{}'::jsonb
           OR NEW.fingerprint IS DISTINCT FROM OLD.fingerprint THEN
            RAISE EXCEPTION 'Setup credential scrub requires its terminal fence'
                USING ERRCODE='23514';
        END IF;
        -- Turning Slack off during setup discards the staged Slack token at
        -- once (#1499): only the web, for the attempt's owner, while it is
        -- collecting and its saved Slack step is off. Every other scrub
        -- needs the terminal fence, by the system.
        IF attempt.state='collecting' THEN
            IF current_user<>'pk_stewardship_web' OR NEW.target<>'slack'
               OR NEW.actor_id IS DISTINCT FROM attempt.owner_id
               OR NOT EXISTS (SELECT 1 FROM public.stewardship_setup_draft_section slack
                    WHERE slack.attempt_id=attempt.id AND slack.step='slack'
                        AND slack.scrubbed_at IS NULL
                        AND slack.values->'enabled'='false'::jsonb) THEN
                RAISE EXCEPTION 'Only a setup with Slack turned off discards its Slack token'
                    USING ERRCODE='23514';
            END IF;
        ELSIF attempt.state NOT IN ('expired','completed')
           OR NEW.actor_id IS NOT NULL THEN
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
            -- Setup's credential steps accept a sign-in up to an hour old
            -- (#1402), the hour inclusive, as the web's check is.
            AND login.authenticated_at>=stamp-interval '60 minutes'
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
    -- The guard is the one overload, runs as its caller with its pinned
    -- search_path, keeps its grants and has its new text.
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname='stewardship_setup_secret_guard_v1')<>1
       OR (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
           WHERE n.nspname='public' AND p.proname='stewardship_setup_secret_guard_v1'
             AND pg_get_function_identity_arguments(p.oid)=''
             AND p.prorettype='trigger'::regtype
             AND NOT p.prosecdef
             AND p.proconfig @> ARRAY['search_path=pg_catalog, public, pg_temp']
             AND coalesce(p.proacl::text,'default')=current_setting('stewardship.migration_0111_acl')
             AND p.prosrc LIKE '%IF attempt.state=''collecting'' THEN%'
             AND p.prosrc LIKE '%IF current_user<>''pk_stewardship_web'' OR NEW.target<>''slack''%'
             AND p.prosrc LIKE '%AND slack.values->''enabled''=''false''::jsonb) THEN%'
             AND p.prosrc LIKE '%ELSIF attempt.state NOT IN (''expired'',''completed'')%'
             AND p.prosrc LIKE '%AND login.authenticated_at>=stamp-interval ''60 minutes''%'
             AND p.prosrc LIKE '%Scrubbed setup credentials cannot be repopulated%')<>1 THEN
        RAISE EXCEPTION 'Migration 0111: the setup credential guard is not installed as declared';
    END IF;
    -- The old constraint is gone and the partial unique index is in place.
    IF EXISTS (SELECT 1 FROM pg_constraint
        WHERE conrelid='public.stewardship_setup_sealed_credential'::regclass
          AND conname='setup_secret_attempt_target')
       OR NOT EXISTS (SELECT 1 FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid
        WHERE i.indrelid='public.stewardship_setup_sealed_credential'::regclass
          AND c.relname='setup_secret_one_live_target' AND i.indisunique AND i.indisvalid
          AND pg_get_indexdef(i.indexrelid)='CREATE UNIQUE INDEX setup_secret_one_live_target ON public.stewardship_setup_sealed_credential USING btree (attempt_id, target) WHERE (scrubbed_at IS NULL)') THEN
        RAISE EXCEPTION 'Migration 0111: the staged credential uniqueness is not installed as declared';
    END IF;
END $check$;
