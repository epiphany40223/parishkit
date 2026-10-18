-- Frozen forward migration file 0169 (the repository-wide file sequence):
-- each setup test is bound to its own inputs (#1578). This file is
-- installed by the Django migration that names it in FROZEN_SQL and must
-- never change once released; tests/stewardship/test_schema_migration_files.py
-- pins its digest and checks that its copies of the replaced functions equal
-- the fresh-install baseline's (schema/functions.sql).
--
-- The setup wizard now runs Test email right after Google Workspace
-- connection and Test Slack right after the Slack step, so a test can no
-- longer need the parish data load or the exact draft version: saving the
-- logos, access or ParishSoft key after a test must keep it. A test is
-- bound instead to the staged credential it used (id, version and key
-- fingerprint, unscrubbed) and to the public settings sealed with that
-- credential: the mail and Testing sections' scope digests for the email
-- test, the Slack section's for the Slack test.
--
-- This file replaces, each copied whole from its latest definition with
-- only this change:
--   stewardship_setup_mail_live_v1 and   0102's bodies without the join on
--   stewardship_setup_slack_live_v1      a succeeded load and without
--                                        attempt.version=$2; the login,
--                                        owner, Administrator rule,
--                                        credential and section-scope
--                                        checks stay. The attempt_version
--                                        argument stays, unused, so every
--                                        caller keeps its signature.
--   stewardship_setup_readiness_guard_v1 0055's body with the email and
--                                        Slack test checks bound to the
--                                        credential and its sealed settings
--                                        instead of the frozen draft version
--                                        and candidate digest.
-- None is SECURITY DEFINER; each keeps its identity, attributes, trigger and
-- grants. No table, column or grant changes. The file ends with a DO block
-- that refuses to commit unless all of this is installed. Reversing needs
-- its own forward migration.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

-- The replaced functions' grants before the replacement, compared in the DO
-- block. A transaction-local setting holds them, not a temporary table: the
-- migration login has no TEMP privilege.
SELECT set_config('stewardship.migration_0169_acl', (
    SELECT string_agg(p.proname || '=' || coalesce(p.proacl::text,'default'), ';' ORDER BY p.proname)
    FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
    WHERE n.nspname='public' AND p.proname IN ('stewardship_setup_mail_live_v1',
        'stewardship_setup_slack_live_v1','stewardship_setup_readiness_guard_v1')), true);

CREATE OR REPLACE FUNCTION public.stewardship_setup_mail_live_v1(attempt_id uuid, attempt_version bigint, credential_id uuid, credential_version bigint, fingerprint text) RETURNS boolean
    LANGUAGE sql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $_$
    SELECT EXISTS (
        SELECT 1 FROM public.stewardship_setup_attempt attempt
        JOIN public.stewardship_system_configuration runtime
            ON runtime.active_configuration_id=attempt.base_id
            AND runtime.mode='testing' AND NOT runtime.restore_review_required
            AND runtime.current_campaign_id IS NULL
        JOIN public.stewardship_configuration_version base
            ON base.id=runtime.active_configuration_id
            AND base.validation_schema IN ('bootstrap-policy-v1', 'bootstrap-users-v10')
        JOIN public.stewardship_portal_session login
            ON login.id=attempt.session_id AND login.principal_id=attempt.owner_id
            AND login.revoked_at IS NULL AND login.expires_at>clock_timestamp()
            AND login.last_activity_at>clock_timestamp()-interval '60 minutes'
        JOIN public.stewardship_portal_user owner
            ON owner.id=attempt.owner_id AND NOT owner.disabled
        JOIN public.stewardship_address_rule rule
            ON rule.configuration_id=base.id AND rule.email=owner.email
            AND rule.roles @> '["administrator"]'::jsonb
        JOIN public.stewardship_setup_sealed_credential candidate
            ON candidate.id=$3 AND candidate.attempt_id=attempt.id
            AND candidate.version=$4 AND candidate.fingerprint=$5
            AND candidate.target='google_workspace' AND candidate.scrubbed_at IS NULL
        JOIN public.stewardship_setup_draft_section mail
            ON mail.attempt_id=attempt.id AND mail.step='mail'
            AND mail.scrubbed_at IS NULL
        JOIN public.stewardship_setup_draft_section testing
            ON testing.attempt_id=attempt.id AND testing.step='testing'
            AND testing.scrubbed_at IS NULL
        -- A test needs only its own inputs (#1578): no load, and no exact
        -- draft version ($2 stays for its callers and binds nothing), so
        -- saving another step keeps a queued test live.
        WHERE attempt.id=$1 AND attempt.state='collecting'
            AND mail.scope_digest=encode(sha256(jsonb_send(jsonb_build_object(
                'sender',candidate.settings->>'sender',
                'reply_to',candidate.settings->>'reply_to',
                'delegated_email',candidate.settings->>'delegated_email'))),'hex')
            AND testing.scope_digest=encode(sha256(jsonb_send(jsonb_build_object(
                'testing_recipient',candidate.settings->>'recipient'))),'hex')
    );
$_$;

CREATE OR REPLACE FUNCTION public.stewardship_setup_slack_live_v1(attempt_id uuid, attempt_version bigint, credential_id uuid, credential_version bigint, fingerprint text) RETURNS boolean
    LANGUAGE sql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $_$
    SELECT EXISTS (
        SELECT 1 FROM public.stewardship_setup_attempt attempt
        JOIN public.stewardship_system_configuration runtime
            ON runtime.active_configuration_id=attempt.base_id
            AND runtime.mode='testing' AND NOT runtime.restore_review_required
            AND runtime.current_campaign_id IS NULL
        JOIN public.stewardship_configuration_version base
            ON base.id=runtime.active_configuration_id
            AND base.validation_schema IN ('bootstrap-policy-v1', 'bootstrap-users-v10')
        JOIN public.stewardship_portal_session login
            ON login.id=attempt.session_id AND login.principal_id=attempt.owner_id
            AND login.revoked_at IS NULL AND login.expires_at>clock_timestamp()
            AND login.last_activity_at>clock_timestamp()-interval '60 minutes'
        JOIN public.stewardship_portal_user owner
            ON owner.id=attempt.owner_id AND NOT owner.disabled
        JOIN public.stewardship_address_rule rule
            ON rule.configuration_id=base.id AND rule.email=owner.email
            AND rule.roles @> '["administrator"]'::jsonb
        JOIN public.stewardship_setup_sealed_credential candidate
            ON candidate.id=$3 AND candidate.attempt_id=attempt.id
            AND candidate.version=$4 AND candidate.fingerprint=$5
            AND candidate.target='slack' AND candidate.scrubbed_at IS NULL
        JOIN public.stewardship_setup_draft_section slack
            ON slack.attempt_id=attempt.id AND slack.step='slack'
            AND slack.scrubbed_at IS NULL
        -- A test needs only its own inputs (#1578): no load, and no exact
        -- draft version ($2 stays for its callers and binds nothing), so
        -- saving another step keeps a queued test live.
        WHERE attempt.id=$1 AND attempt.state='collecting'
            AND slack.scope_digest=encode(sha256(jsonb_send(jsonb_build_object(
                'enabled',true,'channel_id',candidate.settings->>'channel_id'))),'hex')
    );
$_$;

CREATE OR REPLACE FUNCTION public.stewardship_setup_readiness_guard_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE attempt public.stewardship_setup_attempt%ROWTYPE;
    intent public.stewardship_setup_config_intent%ROWTYPE;
    request public.stewardship_config_request%ROWTYPE;
    slack_enabled boolean;
BEGIN
    IF current_user<>'pk_stewardship_web' OR NOT EXISTS (
        SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid() AND locktype='advisory'
            AND classid=736220 AND objid=1 AND objsubid=2
            AND mode='ExclusiveLock' AND granted) THEN
        RAISE EXCEPTION 'Setup readiness needs its ordered original web owner'
            USING ERRCODE='23514';
    END IF;
    SELECT * INTO intent FROM public.stewardship_setup_config_intent
        WHERE id=NEW.intent_id;
    SELECT * INTO attempt FROM public.stewardship_setup_attempt
        WHERE id=intent.attempt_id;
    SELECT * INTO request FROM public.stewardship_config_request
        WHERE id=intent.request_id;
    IF attempt.state IS DISTINCT FROM 'frozen'
        OR attempt.version IS DISTINCT FROM intent.attempt_version
        OR NEW.actor_id IS DISTINCT FROM attempt.owner_id
        OR request.actor_id IS DISTINCT FROM attempt.owner_id
        OR request.request_schema IS NULL OR request.request_schema NOT IN ('initial-setup-patch-v7', 'initial-setup-users-v10')
        OR NOT EXISTS (SELECT 1 FROM public.stewardship_system_configuration runtime
            WHERE runtime.active_configuration_id=attempt.base_id
                AND runtime.mode='testing' AND NOT runtime.restore_review_required
                AND runtime.current_campaign_id IS NULL)
        OR (SELECT state FROM public.stewardship_config_checkpoint
            WHERE request_id=request.id ORDER BY sequence DESC LIMIT 1)
                IS DISTINCT FROM 'staged'
        OR NOT EXISTS (SELECT 1 FROM public.stewardship_portal_session login
            JOIN public.stewardship_portal_user owner
                ON owner.id=login.principal_id AND NOT owner.disabled
            JOIN public.stewardship_address_rule rule
                ON rule.configuration_id=attempt.base_id AND rule.email=owner.email
                AND rule.roles @> '["administrator"]'::jsonb
            WHERE login.id=attempt.session_id AND login.principal_id=attempt.owner_id
                AND login.revoked_at IS NULL AND login.expires_at>clock_timestamp()
                AND login.last_activity_at>clock_timestamp()-interval '60 minutes')
        THEN
        RAISE EXCEPTION 'Setup readiness requires a live frozen original request'
            USING ERRCODE='23514';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM public.stewardship_setup_source_result result
        JOIN public.stewardship_setup_source_exchange exchange
            ON exchange.id=result.exchange_id AND exchange.attempt_id=attempt.id
            AND exchange.scrubbed_at IS NULL
        JOIN public.stewardship_task_run task ON task.id=exchange.task_id
            AND task.root_id=attempt.source_task_id AND task.state='succeeded'
            AND task.fence=exchange.task_fence
        JOIN public.stewardship_setup_sealed_credential credential
            ON credential.id=exchange.credential_id AND credential.scrubbed_at IS NULL
            AND credential.version=exchange.credential_version
            AND credential.fingerprint=exchange.fingerprint
        JOIN public.stewardship_source_snapshot snapshot
            ON snapshot.id=result.snapshot_id
            AND snapshot.state='ready' AND snapshot.task_id=task.id
            AND snapshot.source_fence=exchange.source_fence
        WHERE result.id=NEW.source_result_id) THEN
        RAISE EXCEPTION 'Setup readiness source is not the exact reviewed catalog'
            USING ERRCODE='23514';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM public.stewardship_setup_mail_delivery delivery
        JOIN public.stewardship_setup_sealed_credential credential
            ON credential.id=delivery.credential_id AND credential.scrubbed_at IS NULL
            AND credential.version=delivery.credential_version
            AND credential.fingerprint=delivery.fingerprint
        JOIN public.stewardship_setup_draft_section mail
            ON mail.attempt_id=attempt.id AND mail.step='mail'
            AND mail.scrubbed_at IS NULL
        JOIN public.stewardship_setup_draft_section testing
            ON testing.attempt_id=attempt.id AND testing.step='testing'
            AND testing.scrubbed_at IS NULL
        -- Bound to its own inputs (#1578): the exact credential and the mail
        -- and Testing settings sealed with it, as the live check binds them.
        WHERE delivery.id=NEW.mail_delivery_id AND delivery.attempt_id=attempt.id
            AND credential.target='google_workspace'
            AND delivery.state='accepted'
            AND mail.scope_digest=encode(sha256(jsonb_send(jsonb_build_object(
                'sender',credential.settings->>'sender',
                'reply_to',credential.settings->>'reply_to',
                'delegated_email',credential.settings->>'delegated_email'))),'hex')
            AND testing.scope_digest=encode(sha256(jsonb_send(jsonb_build_object(
                'testing_recipient',credential.settings->>'recipient'))),'hex')
            AND NEW.testing_recipient=testing.values->>'testing_recipient'
            AND NEW.testing_recipient=credential.settings->>'recipient') THEN
        RAISE EXCEPTION 'Setup readiness requires exact accepted Testing mail'
            USING ERRCODE='23514';
    END IF;
    SELECT (values->>'enabled')::boolean INTO slack_enabled
        FROM public.stewardship_setup_draft_section
        WHERE attempt_id=attempt.id AND step='slack' AND scrubbed_at IS NULL;
    IF slack_enabled IS NULL OR (slack_enabled AND NEW.slack_delivery_id IS NULL)
       OR (NOT slack_enabled AND NEW.slack_delivery_id IS NOT NULL) THEN
        RAISE EXCEPTION 'Setup Slack readiness differs from reviewed settings'
            USING ERRCODE='23514';
    END IF;
    IF slack_enabled AND NOT EXISTS (
        SELECT 1 FROM public.stewardship_setup_slack_delivery delivery
        JOIN public.stewardship_setup_sealed_credential credential
            ON credential.id=delivery.credential_id AND credential.scrubbed_at IS NULL
            AND credential.version=delivery.credential_version
            AND credential.fingerprint=delivery.fingerprint
        JOIN public.stewardship_setup_draft_section slack
            ON slack.attempt_id=attempt.id AND slack.step='slack'
            AND slack.scrubbed_at IS NULL
        -- Bound to its own inputs (#1578): the exact credential and the
        -- channel sealed with it.
        WHERE delivery.id=NEW.slack_delivery_id AND delivery.attempt_id=attempt.id
            AND credential.target='slack'
            AND delivery.state='accepted'
            AND slack.scope_digest=encode(sha256(jsonb_send(jsonb_build_object(
                'enabled',true,'channel_id',credential.settings->>'channel_id'))),'hex')) THEN
        RAISE EXCEPTION 'Setup readiness requires the exact accepted Slack test'
            USING ERRCODE='23514';
    END IF;
    IF EXISTS (SELECT 1 FROM public.stewardship_setup_mail_delivery
        WHERE attempt_id=attempt.id AND state IN ('queued','submitting'))
       OR EXISTS (SELECT 1 FROM public.stewardship_setup_slack_delivery
        WHERE attempt_id=attempt.id AND state IN ('queued','submitting')) THEN
        RAISE EXCEPTION 'Setup tests must finish before confirmation'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;

DO $check$
BEGIN
    -- Each replaced function is the one overload, carries its new text, has
    -- lost the old binding, runs as its caller with its pinned search_path
    -- and keeps its grants.
    IF (SELECT count(*) FROM pg_proc p
        JOIN pg_namespace n ON n.oid=p.pronamespace AND n.nspname='public'
        WHERE p.proname IN ('stewardship_setup_mail_live_v1',
            'stewardship_setup_slack_live_v1','stewardship_setup_readiness_guard_v1'))<>3
       OR (SELECT count(*) FROM pg_proc p
        JOIN pg_namespace n ON n.oid=p.pronamespace AND n.nspname='public'
        JOIN (VALUES
            ('stewardship_setup_mail_live_v1',
             'attempt_id uuid, attempt_version bigint, credential_id uuid, credential_version bigint, fingerprint text',
             'WHERE attempt.id=$1 AND attempt.state=''collecting'''),
            ('stewardship_setup_slack_live_v1',
             'attempt_id uuid, attempt_version bigint, credential_id uuid, credential_version bigint, fingerprint text',
             'WHERE attempt.id=$1 AND attempt.state=''collecting'''),
            ('stewardship_setup_readiness_guard_v1', '',
             'AND credential.target=''google_workspace''')
        ) expected(name, arguments, marker)
          ON p.proname=expected.name
         AND pg_get_function_identity_arguments(p.oid)=expected.arguments
         AND strpos(p.prosrc, expected.marker)>0
         AND strpos(p.prosrc, 'original.state=''succeeded''')=0
         AND strpos(p.prosrc, 'attempt.version=$2')=0
         AND strpos(p.prosrc, 'delivery.candidate_digest=request.candidate_digest')=0
         AND NOT p.prosecdef
         AND p.proconfig @> ARRAY['search_path=pg_catalog, public, pg_temp'])<>3
       OR (SELECT string_agg(p.proname || '=' || coalesce(p.proacl::text,'default'), ';' ORDER BY p.proname)
           FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
           WHERE n.nspname='public' AND p.proname IN ('stewardship_setup_mail_live_v1',
               'stewardship_setup_slack_live_v1','stewardship_setup_readiness_guard_v1'))
          IS DISTINCT FROM current_setting('stewardship.migration_0169_acl') THEN
        RAISE EXCEPTION 'Migration 0169: a replaced setup test function is not installed as declared';
    END IF;
    -- The readiness guard binds the Slack test to its sealed channel and
    -- still runs on readiness inserts.
    IF NOT EXISTS (SELECT 1 FROM pg_proc
            WHERE oid='public.stewardship_setup_readiness_guard_v1()'::regprocedure
              AND strpos(prosrc, 'AND credential.target=''slack''')>0
              AND strpos(prosrc, 'AND mail.scope_digest=encode(')>0
              AND strpos(prosrc, 'AND slack.scope_digest=encode(')>0)
       OR NOT EXISTS (SELECT 1 FROM pg_trigger t
            WHERE t.tgrelid='public.stewardship_setup_readiness_binding'::regclass
              AND t.tgname='stewardship_setup_readiness_insert' AND t.tgenabled='O'
              AND t.tgfoid='public.stewardship_setup_readiness_guard_v1()'::regprocedure) THEN
        RAISE EXCEPTION 'Migration 0169: the setup readiness guard is not installed as declared';
    END IF;
END
$check$;
