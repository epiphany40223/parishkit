-- Frozen forward migration file 0183 (the repository-wide file sequence):
-- the setup wizard's quick parish data check (#2201, admin-portal spec
-- "Checking the parish data during setup"). This file is installed by the
-- Django migration that names it in FROZEN_SQL and must never change once
-- released; tests/stewardship/test_schema_migration_files.py pins its
-- digest and checks each replaced baseline function's copy here against the
-- baseline.
--
-- Setup's Load parish data step read the whole ParishSoft corpus only to
-- unlock Review; Finish setup's final load reads it all again. The step is
-- now Check parish data: the web queues a check, a new
-- stewardship_setup_source_check row bound to the staged ParishSoft key's
-- exact version and fingerprint and to its chosen organization; the
-- isolated ParishSoft installer asks ParishSoft for the organization's name
-- and its Family and Member totals and records them (or a stable error
-- code). The check's guard admits each move for its one owner and only
-- under the work lock: the web's request and discard, each only while the
-- owner's own setup sign-in is live (stewardship_setup_owner_live_v1, 0174),
-- and the installer's claim and answer; an ended setup only discards a check
-- still waiting, which the end-of-setup trigger does. A new trigger on the
-- setup attempt lets it freeze only with its current check. The guard and
-- those two triggers are SECURITY DEFINER, stated again with ALTER as every
-- definer is; the mutable guard is invoker's rights. No Family or Member
-- content is stored.
--
-- The setup readiness record binds the check instead of a staged load: a
-- new source_check_id column, source_result_id becomes nullable (bindings
-- made before this change keep it), and exactly one of the two is set.
-- This file replaces, each with its baseline body and only this change
-- (their final bodies are also the baseline's):
--   stewardship_setup_readiness_guard_v1   requires the attempt's newest
--     ready check of the current staged key and organization, and no
--     staged load (SECURITY INVOKER, as before)
--   stewardship_setup_attempt_guard_v1     freezing no longer needs a
--     succeeded load task (the new freeze trigger needs the check); a load
--     an earlier release bound must have finished (SECURITY INVOKER, as
--     before)
--   stewardship_setup_install_selected_v1  the ParishSoft branch accepts
--     the check's credential binding (or a pre-#2201 binding's staged
--     load) (SECURITY INVOKER, as before: each credential installer reads
--     only the check's binding columns, as it reads the staged load's)
-- CREATE OR REPLACE keeps their owners and grants. Table grants are
-- runtime_grants' (accounts/setup_check_grants.py and
-- accounts/setup_install_grants.py). The file ends with a DO
-- block that refuses to commit unless all of it is installed as declared.
-- Reversing needs its own forward migration.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

-- The three replaced functions' grants before the replacement, compared in
-- the DO block. A transaction-local setting holds them, not a temporary
-- table: the migration login has no TEMP privilege.
SELECT set_config('stewardship.migration_0183_acl', (
    SELECT string_agg(p.proname||'='||coalesce(p.proacl::text,'default'), ';' ORDER BY p.proname)
    FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
    WHERE n.nspname='public'
      AND p.proname IN ('stewardship_setup_readiness_guard_v1','stewardship_setup_attempt_guard_v1',
          'stewardship_setup_install_selected_v1')), true);

-- The check table (accounts/setup_check_models.SetupSourceCheck).
CREATE TABLE "stewardship_setup_source_check" ("id" uuid NOT NULL PRIMARY KEY, "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "actor_id" uuid NULL, "correlation_id" uuid NOT NULL, "updated_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "version" bigint NOT NULL CHECK ("version" >= 0), "attempt_id" uuid NOT NULL, "credential_id" uuid NOT NULL, "credential_version" bigint NOT NULL CHECK ("credential_version" >= 0), "fingerprint" varchar(64) NOT NULL, "organization_id" bigint NOT NULL CHECK ("organization_id" >= 0), "state" varchar(16) NOT NULL, "error_code" varchar(48) NULL, "organization_name" varchar(200) NULL, "family_count" bigint NULL CHECK ("family_count" >= 0), "member_count" bigint NULL CHECK ("member_count" >= 0), "worker_id" uuid NULL, "started_at" timestamp with time zone NULL, "deadline_at" timestamp with time zone NULL, "finished_at" timestamp with time zone NULL, CONSTRAINT "stewardship_accounts_setupsourcecheck_positive_version" CHECK ("version" >= 1), CONSTRAINT "setup_check_known_state" CHECK (((state)::text = ANY (ARRAY[('queued'::character varying)::text, ('running'::character varying)::text, ('ready'::character varying)::text, ('failed'::character varying)::text, ('discarded'::character varying)::text]))), CONSTRAINT "setup_check_binding_shape" CHECK (("credential_version" >= 1 AND "fingerprint"::text ~ '^[0-9a-f]{64}$' AND "organization_id" >= 1 AND "organization_id" < 2147483648)), CONSTRAINT "setup_check_error_shape" CHECK ((((NOT ((state)::text = 'failed'::text)) AND (error_code IS NULL)) OR (((error_code)::text = ANY (ARRAY[('parishsoft_key_refused'::character varying)::text, ('parishsoft_organization_not_reached'::character varying)::text, ('parishsoft_no_families'::character varying)::text, ('parishsoft_check_unreadable'::character varying)::text, ('parishsoft_unavailable'::character varying)::text])) AND ((state)::text = 'failed'::text)))), CONSTRAINT "setup_check_result_shape" CHECK ((("error_code" IS NULL AND "family_count" >= 1 AND "family_count" <= 2147483647 AND "finished_at" IS NOT NULL AND "member_count" >= 0 AND "member_count" <= 2147483647 AND "state" = 'ready') OR (NOT ("state" = 'ready') AND "family_count" IS NULL AND "member_count" IS NULL AND "organization_name" IS NULL))));
ALTER TABLE "stewardship_setup_source_check" ADD CONSTRAINT "stewardship_setup_so_attempt_id_a26b9e14_fk_stewardsh" FOREIGN KEY ("attempt_id") REFERENCES "stewardship_setup_attempt" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_setup_source_check" ADD CONSTRAINT "stewardship_setup_so_credential_id_86e3e36a_fk_stewardsh" FOREIGN KEY ("credential_id") REFERENCES "stewardship_setup_sealed_credential" ("id") DEFERRABLE INITIALLY DEFERRED;
CREATE UNIQUE INDEX "setup_check_one_waiting" ON "stewardship_setup_source_check" ("attempt_id") WHERE ((state)::text = ANY (ARRAY[('queued'::character varying)::text, ('running'::character varying)::text]));
CREATE INDEX "stewardship_setup_source_check_correlation_id_955b97f6" ON "stewardship_setup_source_check" ("correlation_id");
CREATE INDEX "stewardship_setup_source_check_attempt_id_a26b9e14" ON "stewardship_setup_source_check" ("attempt_id");
CREATE INDEX "stewardship_setup_source_check_credential_id_86e3e36a" ON "stewardship_setup_source_check" ("credential_id");

-- Every update advances the version and keeps the identity and bindings,
-- in the quoted per-column form every generated mutable guard uses, which
-- test_all_concrete_mutable_records_have_enabled_guard checks against the
-- model's immutable and write-once fields.
CREATE FUNCTION public.stewardship_setup_source_check_mutable_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF NEW."id" IS DISTINCT FROM OLD."id"
       OR NEW."created_at" IS DISTINCT FROM OLD."created_at"
       OR NEW."attempt_id" IS DISTINCT FROM OLD."attempt_id"
       OR NEW."credential_id" IS DISTINCT FROM OLD."credential_id"
       OR NEW."credential_version" IS DISTINCT FROM OLD."credential_version"
       OR NEW."fingerprint" IS DISTINCT FROM OLD."fingerprint"
       OR NEW."organization_id" IS DISTINCT FROM OLD."organization_id"
       OR (OLD."worker_id" IS NOT NULL AND NEW."worker_id" IS DISTINCT FROM OLD."worker_id")
       OR (OLD."started_at" IS NOT NULL AND NEW."started_at" IS DISTINCT FROM OLD."started_at")
       OR (OLD."deadline_at" IS NOT NULL AND NEW."deadline_at" IS DISTINCT FROM OLD."deadline_at")
       OR (OLD."finished_at" IS NOT NULL AND NEW."finished_at" IS DISTINCT FROM OLD."finished_at") THEN
        RAISE EXCEPTION 'Record identity and bindings are immutable' USING ERRCODE='23514';
    END IF;
    IF NEW.version IS DISTINCT FROM OLD.version + 1 THEN
        RAISE EXCEPTION 'Every update must advance the record version' USING ERRCODE='23514';
    END IF;
    NEW.updated_at:=statement_timestamp();
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.stewardship_setup_source_check_mutable_v1() FROM PUBLIC;
CREATE TRIGGER stewardship_setup_source_check_mutable_guard_v1 BEFORE UPDATE
    ON public.stewardship_setup_source_check
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_source_check_mutable_v1();

-- The check's guard, with owner rights: the installer reads neither the
-- lookup nor the session tables, and the end-of-setup discard runs for
-- whichever login ends setup. Every write needs the work lock. INSERT: only
-- the web login, for the attempt's owner while it is collecting and their
-- setup sign-in is live, bound to its unscrubbed staged ParishSoft key's
-- exact version, fingerprint and chosen organization, with no organization
-- lookup still open, queued and empty. UPDATE of a waiting check: by the
-- ParishSoft installer, queued to running (its claim, for the key still
-- staged as checked) and running to ready (the name and two totals) or
-- failed (an error code); by the web, for the owner while collecting,
-- queued or running to discarded; and, once setup has ended, the discard.
-- A finished check (ready, failed or discarded) never changes. DELETE:
-- never.
CREATE FUNCTION public.stewardship_setup_source_check_guard_v1() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE attempt public.stewardship_setup_attempt%ROWTYPE;
    credential public.stewardship_setup_sealed_credential%ROWTYPE;
BEGIN
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'Parish data checks are retained' USING ERRCODE='23514';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
        AND mode='ExclusiveLock' AND granted) THEN
        RAISE EXCEPTION 'Parish data checks require ordered ownership'
            USING ERRCODE='23514';
    END IF;
    SELECT * INTO attempt FROM public.stewardship_setup_attempt WHERE id=NEW.attempt_id;
    SELECT * INTO credential FROM public.stewardship_setup_sealed_credential
        WHERE id=NEW.credential_id;
    IF TG_OP='INSERT' THEN
        IF session_user<>'pk_stewardship_web' OR NEW.state<>'queued' OR NEW.version<>1
           OR attempt.state IS DISTINCT FROM 'collecting'
           OR NEW.actor_id IS DISTINCT FROM attempt.owner_id
           OR credential.attempt_id IS DISTINCT FROM NEW.attempt_id
           OR credential.target IS DISTINCT FROM 'parishsoft'
           OR credential.scrubbed_at IS NOT NULL
           OR credential.version IS DISTINCT FROM NEW.credential_version
           OR credential.fingerprint IS DISTINCT FROM NEW.fingerprint
           OR (credential.settings OPERATOR(pg_catalog.->>) 'organization_id')
               IS DISTINCT FROM NEW.organization_id::text
           OR EXISTS (SELECT 1 FROM public.stewardship_setup_parishsoft_lookup lookup
               WHERE lookup.attempt_id=NEW.attempt_id
                 AND lookup.state IN ('queued','running','ready','failed'))
           OR NEW.error_code IS NOT NULL OR NEW.organization_name IS NOT NULL
           OR NEW.family_count IS NOT NULL OR NEW.member_count IS NOT NULL
           OR NEW.worker_id IS NOT NULL OR NEW.started_at IS NOT NULL
           OR NEW.deadline_at IS NOT NULL OR NEW.finished_at IS NOT NULL
           OR NOT public.stewardship_setup_owner_live_v1(NEW.attempt_id) THEN
            RAISE EXCEPTION 'A parish data check needs its setup''s staged key, chosen organization and Administrator'
                USING ERRCODE='23514';
        END IF;
        -- The newest check decides the step, so its order is the database's.
        NEW.created_at:=statement_timestamp(); NEW.updated_at:=NEW.created_at;
        RETURN NEW;
    END IF;
    IF OLD.state NOT IN ('queued','running') THEN
        RAISE EXCEPTION 'A finished parish data check cannot change' USING ERRCODE='23514';
    END IF;
    IF attempt.state IN ('expired','completed') THEN
        -- The end-of-setup discard (stewardship_setup_discard_checks_v1).
        IF NEW.state<>'discarded' OR NEW.actor_id IS NOT NULL
           OR NEW.error_code IS NOT NULL OR NEW.organization_name IS NOT NULL
           OR NEW.family_count IS NOT NULL OR NEW.member_count IS NOT NULL THEN
            RAISE EXCEPTION 'An ended setup only discards its parish data check'
                USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    IF attempt.state IS DISTINCT FROM 'collecting' THEN
        RAISE EXCEPTION 'A parish data check changes only while setup collects'
            USING ERRCODE='23514';
    END IF;
    IF session_user='pk_stewardship_credential_parishsoft' THEN
        IF NEW.actor_id IS NOT NULL THEN
            RAISE EXCEPTION 'The ParishSoft installer only answers a parish data check'
                USING ERRCODE='23514';
        ELSIF OLD.state='queued' AND NEW.state='running' THEN
            IF NEW.worker_id IS NULL OR NEW.started_at IS NULL
               OR NEW.deadline_at IS NULL OR NEW.deadline_at<=NEW.started_at
               OR NEW.deadline_at>NEW.started_at+interval '5 minutes'
               OR NEW.finished_at IS NOT NULL OR NEW.error_code IS NOT NULL
               OR NEW.organization_name IS NOT NULL OR NEW.family_count IS NOT NULL
               OR NEW.member_count IS NOT NULL
               OR credential.scrubbed_at IS NOT NULL
               OR credential.version IS DISTINCT FROM NEW.credential_version
               OR credential.fingerprint IS DISTINCT FROM NEW.fingerprint THEN
                RAISE EXCEPTION 'A parish data check is claimed only for its current staged key'
                    USING ERRCODE='23514';
            END IF;
        ELSIF OLD.state='running' AND NEW.state='ready' THEN
            IF NEW.finished_at IS NULL OR NEW.error_code IS NOT NULL
               OR NEW.family_count IS NULL OR NEW.member_count IS NULL
               OR (NEW.organization_name IS NOT NULL
                   AND length(NEW.organization_name) NOT BETWEEN 1 AND 200) THEN
                RAISE EXCEPTION 'A parish data check answer needs its two totals'
                    USING ERRCODE='23514';
            END IF;
        ELSIF OLD.state='running' AND NEW.state='failed' THEN
            IF NEW.finished_at IS NULL OR NEW.error_code IS NULL THEN
                RAISE EXCEPTION 'A failed parish data check keeps only its error code'
                    USING ERRCODE='23514';
            END IF;
        ELSE
            RAISE EXCEPTION 'Invalid parish data check transition' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    IF session_user<>'pk_stewardship_web' OR NEW.state<>'discarded'
       OR NEW.actor_id IS DISTINCT FROM attempt.owner_id
       OR NEW.error_code IS NOT NULL OR NEW.organization_name IS NOT NULL
       OR NEW.family_count IS NOT NULL OR NEW.member_count IS NOT NULL
       OR NOT public.stewardship_setup_owner_live_v1(NEW.attempt_id) THEN
        RAISE EXCEPTION 'Only the setup''s Administrator discards a parish data check'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
ALTER FUNCTION public.stewardship_setup_source_check_guard_v1() SECURITY DEFINER;
REVOKE ALL ON FUNCTION public.stewardship_setup_source_check_guard_v1() FROM PUBLIC;
CREATE TRIGGER stewardship_setup_source_check_guard BEFORE INSERT OR UPDATE OR DELETE
    ON public.stewardship_setup_source_check
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_source_check_guard_v1();

-- Ending setup discards a check still waiting for its answer, with owner
-- rights, as the lookups are scrubbed (stewardship_setup_scrub_lookups_v1),
-- so the installer never claims a check of an ended setup.
CREATE FUNCTION public.stewardship_setup_discard_checks_v1() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF NEW.state IN ('expired','completed') AND NEW.state<>OLD.state THEN
        UPDATE public.stewardship_setup_source_check SET state='discarded',
            actor_id=NULL, correlation_id=NEW.correlation_id, version=version+1
            WHERE attempt_id=NEW.id AND state IN ('queued','running');
    END IF;
    RETURN NULL;
END $$;
ALTER FUNCTION public.stewardship_setup_discard_checks_v1() SECURITY DEFINER;
REVOKE ALL ON FUNCTION public.stewardship_setup_discard_checks_v1() FROM PUBLIC;
CREATE TRIGGER stewardship_setup_discard_checks AFTER UPDATE
    ON public.stewardship_setup_attempt
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_discard_checks_v1();

-- Freezing a setup needs its current parish data check (#2201): the
-- attempt's newest check that was not discarded is ready, of its
-- unscrubbed staged ParishSoft key at the exact version, fingerprint and
-- organization it checked. The attempt guard no longer needs a succeeded
-- load task to freeze, so this trigger holds the freeze to the check, as the
-- readiness guard in the same transaction does. With owner rights, as
-- 0177's load check is: the scheduler and worker also update setup
-- attempts and may not read the check table (#2163).
CREATE FUNCTION public.stewardship_setup_freeze_check_v1() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF NEW.state='frozen' AND OLD.state IS DISTINCT FROM 'frozen' AND NOT EXISTS (
        SELECT 1 FROM public.stewardship_setup_source_check checked
        JOIN public.stewardship_setup_sealed_credential credential
            ON credential.id=checked.credential_id AND credential.attempt_id=NEW.id
            AND credential.target='parishsoft' AND credential.scrubbed_at IS NULL
            AND credential.version=checked.credential_version
            AND credential.fingerprint=checked.fingerprint
            AND (credential.settings OPERATOR(pg_catalog.->>) 'organization_id')
                =checked.organization_id::text
        WHERE checked.attempt_id=NEW.id AND checked.state='ready'
            AND NOT EXISTS (SELECT 1 FROM public.stewardship_setup_source_check later
                WHERE later.attempt_id=NEW.id AND later.state<>'discarded'
                    AND later.created_at>checked.created_at)) THEN
        RAISE EXCEPTION 'Setup freezes only with its current parish data check (#2201)'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
ALTER FUNCTION public.stewardship_setup_freeze_check_v1() SECURITY DEFINER;
REVOKE ALL ON FUNCTION public.stewardship_setup_freeze_check_v1() FROM PUBLIC;
CREATE TRIGGER stewardship_setup_freeze_check BEFORE UPDATE
    ON public.stewardship_setup_attempt
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_freeze_check_v1();

-- The readiness record binds the check (accounts/setup_readiness_models).
ALTER TABLE "stewardship_setup_readiness_binding" ALTER COLUMN "source_result_id" DROP NOT NULL;
ALTER TABLE "stewardship_setup_readiness_binding" ADD COLUMN "source_check_id" uuid NULL CONSTRAINT "stewardship_setup_re_source_check_id_ef620aa7_fk_stewardsh" REFERENCES "stewardship_setup_source_check"("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_setup_readiness_binding" ADD CONSTRAINT "setup_readiness_one_source" CHECK ((("source_check_id" IS NOT NULL AND "source_result_id" IS NULL) OR ("source_check_id" IS NULL AND "source_result_id" IS NOT NULL)));
CREATE INDEX "stewardship_setup_readiness_binding_source_check_id_ef620aa7" ON "stewardship_setup_readiness_binding" ("source_check_id");

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
                AND rule.roles OPERATOR(pg_catalog.@>) '["administrator"]'::jsonb
            WHERE login.id=attempt.session_id AND login.principal_id=attempt.owner_id
                AND login.revoked_at IS NULL AND login.expires_at>clock_timestamp()
                AND login.last_activity_at>clock_timestamp()-interval '60 minutes')
        THEN
        RAISE EXCEPTION 'Setup readiness requires a live frozen original request'
            USING ERRCODE='23514';
    END IF;
    -- The source is the attempt's newest parish data check (#2201): ready,
    -- of its unscrubbed staged ParishSoft key at the exact version,
    -- fingerprint and organization it checked, with no later check of the
    -- attempt that was not discarded. A staged load is never bound any more;
    -- the final load at Finish setup is the only full load.
    IF NEW.source_result_id IS NOT NULL OR NOT EXISTS (
        SELECT 1 FROM public.stewardship_setup_source_check checked
        JOIN public.stewardship_setup_sealed_credential credential
            ON credential.id=checked.credential_id AND credential.attempt_id=attempt.id
            AND credential.target='parishsoft' AND credential.scrubbed_at IS NULL
            AND credential.version=checked.credential_version
            AND credential.fingerprint=checked.fingerprint
            AND (credential.settings OPERATOR(pg_catalog.->>) 'organization_id')
                =checked.organization_id::text
        WHERE checked.id=NEW.source_check_id AND checked.attempt_id=attempt.id
            AND checked.state='ready'
            AND NOT EXISTS (SELECT 1 FROM public.stewardship_setup_source_check later
                WHERE later.attempt_id=attempt.id AND later.state<>'discarded'
                    AND later.created_at>checked.created_at)) THEN
        RAISE EXCEPTION 'Setup readiness source is not the current parish data check'
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

CREATE OR REPLACE FUNCTION public.stewardship_setup_attempt_guard_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE stamp timestamptz:=clock_timestamp(); login stewardship_portal_session%ROWTYPE;
        task stewardship_task_run%ROWTYPE; selected uuid; live boolean; reason text;
        previous text;
BEGIN
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'Setup history cannot be deleted' USING ERRCODE='23514';
    END IF;
    -- The end state of the load bound before this change, if any (#1353).
    IF TG_OP='UPDATE' AND OLD.source_task_id IS NOT NULL THEN
        SELECT state INTO previous FROM stewardship_task_run WHERE id=OLD.source_task_id;
    END IF;

    IF TG_OP='UPDATE' AND NEW.state='completed' THEN
        IF current_user<>'pk_stewardship_worker' OR OLD.state<>'frozen'
            OR NEW.actor_id IS DISTINCT FROM OLD.owner_id
            OR (to_jsonb(NEW)-ARRAY['state','actor_id','correlation_id','version','updated_at'])
                IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['state','actor_id','correlation_id','version','updated_at'])
            OR NOT EXISTS (
                SELECT 1 FROM public.stewardship_setup_completion completed
                JOIN public.stewardship_setup_prepared prepared ON prepared.id=completed.preparation_id
                JOIN public.stewardship_setup_readiness_binding ready ON ready.id=prepared.readiness_id
                JOIN public.stewardship_setup_config_intent intent ON intent.id=ready.intent_id
                WHERE intent.attempt_id=OLD.id AND intent.attempt_version=OLD.version
                    AND prepared.id=public.stewardship_setup_completion_context_v1()
            ) THEN
            RAISE EXCEPTION 'Setup completion requires its atomic finalization receipt'
                USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    IF current_user='pk_stewardship_worker' THEN
        IF TG_OP<>'UPDATE' OR OLD.state<>'loading' OR NEW.state<>'collecting'
           OR NEW.actor_id IS DISTINCT FROM OLD.owner_id
           OR NEW.renewed_at IS DISTINCT FROM OLD.renewed_at
           OR NEW.source_task_id IS DISTINCT FROM OLD.source_task_id
           -- A failed load also returns setup to collecting (#1353), so the
           -- Admin can correct the ParishSoft connection and load again.
           OR (previous IS DISTINCT FROM 'failed' AND NOT EXISTS (
                SELECT 1 FROM public.stewardship_setup_source_result result
                JOIN public.stewardship_setup_source_exchange exchange
                    ON exchange.id=result.exchange_id AND exchange.attempt_id=OLD.id
                    AND exchange.scrubbed_at IS NULL
                JOIN public.stewardship_task_run finished
                    ON finished.id=exchange.task_id AND finished.state='succeeded'
                    AND finished.root_id=OLD.source_task_id
                    AND finished.fence=exchange.task_fence
                JOIN public.stewardship_source_snapshot snapshot
                    ON snapshot.id=result.snapshot_id AND snapshot.state='ready'
                    AND snapshot.task_id=finished.id
                    AND snapshot.source_fence=exchange.source_fence
            )) THEN
            RAISE EXCEPTION 'Worker may only acknowledge validated setup staging'
                USING ERRCODE='23514';
        END IF;
    END IF;
    IF current_user='pk_stewardship_scheduler' AND
       (TG_OP<>'UPDATE' OR NEW.state<>'expired' OR NEW.actor_id IS NOT NULL) THEN
        RAISE EXCEPTION 'Scheduler may only expire setup metadata'
            USING ERRCODE='23514';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
        AND mode='ExclusiveLock' AND granted) THEN
        RAISE EXCEPTION 'Setup mutation requires ordered ownership' USING ERRCODE='23514';
    END IF;
    SELECT active_configuration_id INTO selected FROM stewardship_system_configuration
        WHERE mode='testing' AND NOT restore_review_required;
    SELECT s.id,s.principal_id,s.revoked_at,s.expires_at,s.last_activity_at
        INTO login.id,login.principal_id,login.revoked_at,
             login.expires_at,login.last_activity_at
        FROM stewardship_portal_session s
        JOIN stewardship_portal_user u ON u.id=s.principal_id AND NOT u.disabled
        JOIN stewardship_address_rule rule ON rule.configuration_id=selected
            AND rule.email=u.email AND rule.roles OPERATOR(pg_catalog.@>) '["administrator"]'::jsonb
        WHERE s.id=NEW.session_id AND s.principal_id=NEW.owner_id;
    live=login.id IS NOT NULL AND login.revoked_at IS NULL
        AND login.expires_at>stamp AND login.last_activity_at>stamp-interval '60 minutes';
    IF TG_OP='INSERT' THEN
        IF NEW.state<>'collecting' OR NEW.source_task_id IS NOT NULL
           OR NEW.renewed_at IS NOT NULL OR NEW.version<>1 OR NOT live
           OR NEW.actor_id IS DISTINCT FROM NEW.owner_id
           OR selected IS DISTINCT FROM NEW.base_id
           OR NOT EXISTS (SELECT 1 FROM stewardship_configuration_version
                WHERE id=selected AND validation_schema IN ('bootstrap-policy-v1', 'bootstrap-users-v10')) THEN
            RAISE EXCEPTION 'Setup requires the original live bootstrap Admin session'
                USING ERRCODE='23514';
        END IF;
        NEW.created_at=stamp; NEW.updated_at=stamp;
        RETURN NEW;
    END IF;
    IF OLD.state IN ('expired','completed') OR NEW.state='completed' THEN
        RAISE EXCEPTION 'Setup completion requires its configured-marker owner'
            USING ERRCODE='23514';
    END IF;
    IF NEW.source_task_id IS NOT NULL THEN
        SELECT * INTO task FROM stewardship_task_run WHERE id=NEW.source_task_id;
        IF task.task_type IS DISTINCT FROM 'setup_source_load'
           OR task.domain_request_id IS DISTINCT FROM NEW.id
           OR task.initiated_by_id IS DISTINCT FROM NEW.owner_id
           OR task.created_at<NEW.created_at OR task.root_id<>task.id THEN
            RAISE EXCEPTION 'Setup source identity is not the original bound load'
                USING ERRCODE='23514';
        END IF;
    END IF;
    reason=CASE
        WHEN login.id IS NULL OR login.revoked_at IS NOT NULL THEN 'session'
        WHEN stamp>=login.expires_at THEN 'absolute'
        WHEN OLD.state='loading' AND stamp>=task.created_at+interval '2 hours' THEN 'watchdog'
        WHEN stamp>=login.last_activity_at+interval '60 minutes' THEN 'idle'
        ELSE NULL END;
    IF NEW.state='expired' THEN
        IF reason IS NULL THEN
            IF NOT live OR NEW.actor_id IS DISTINCT FROM NEW.owner_id THEN
                RAISE EXCEPTION 'Only the owning live Admin can cancel setup'
                    USING ERRCODE='23514';
            END IF;
            reason='cancelled';
        END IF;
        NEW.expiry_reason=reason; NEW.expired_at=stamp;
        IF NEW.renewed_at IS DISTINCT FROM OLD.renewed_at
           OR NEW.source_task_id IS DISTINCT FROM OLD.source_task_id THEN
            RAISE EXCEPTION 'Setup expiry cannot change its retained bindings'
                USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    IF reason IS NOT NULL OR NOT live THEN
        RAISE EXCEPTION 'Expired setup cannot accept later work' USING ERRCODE='23514';
    END IF;
    IF OLD.state<>'frozen' AND (selected IS DISTINCT FROM NEW.base_id
        OR NEW.actor_id IS DISTINCT FROM NEW.owner_id) THEN
        RAISE EXCEPTION 'Setup no longer has its original authorized base'
            USING ERRCODE='23514';
    END IF;
    IF OLD.state='frozen' OR (
        (OLD.state=NEW.state AND NEW.state IN ('collecting','loading'))
        OR (OLD.state='collecting' AND NEW.state='loading' AND OLD.source_task_id IS NULL)
        -- Only the worker returns a failed load (#1353), in its failure transaction.
        OR (OLD.state='loading' AND NEW.state='collecting' AND (task.state='succeeded'
            OR (task.state='failed' AND current_user='pk_stewardship_worker')))
        -- Freezing needs no load since #2201: the readiness guard, in the
        -- same transaction, requires the current parish data check. A load
        -- an earlier release bound must have finished.
        OR (OLD.state='collecting' AND NEW.state='frozen'
            AND (NEW.source_task_id IS NULL OR task.state IN ('succeeded','failed')))) IS NOT TRUE THEN
        RAISE EXCEPTION 'Setup transition has no completed owning work'
            USING ERRCODE='23514';
    END IF;
    -- Discarding a finished load (#1353): the owning live Admin (checked
    -- above) clears the binding of a load that succeeded or failed, never of
    -- one still running, and its renewal time with it, so the next load
    -- starts a fresh watchdog and renewal clock. The audit trigger records
    -- why. Only an unbound collecting attempt can bind a new load.
    IF OLD.state='collecting' AND NEW.state='collecting'
       AND OLD.source_task_id IS NOT NULL AND NEW.source_task_id IS NULL THEN
        IF previous IS NULL OR previous NOT IN ('succeeded','failed')
           OR NEW.renewed_at IS NOT NULL THEN
            RAISE EXCEPTION 'Only a finished setup load can be discarded'
                USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.source_task_id IS DISTINCT FROM OLD.source_task_id
       AND NOT (OLD.state='collecting' AND NEW.state='loading') THEN
        RAISE EXCEPTION 'Setup load binding requires its loading transition'
            USING ERRCODE='23514';
    END IF;
    IF NEW.renewed_at IS DISTINCT FROM OLD.renewed_at THEN
        IF OLD.state<>'loading' OR NEW.state<>'loading' OR task.state<>'running'
           OR task.lease_expires_at<=stamp OR task.heartbeat_at IS NULL
           OR task.heartbeat_at<=stamp-interval '90 seconds'
           OR stamp<coalesce(OLD.renewed_at,task.created_at)+interval '5 minutes'
           OR stamp>=task.created_at+interval '2 hours'
           OR NOT EXISTS (SELECT 1 FROM stewardship_source_lease lease
               WHERE lease.owner_id=task.id AND lease.task_fence=task.fence
                 AND lease.worker_id=task.worker_id AND lease.expires_at>stamp
                 AND lease.heartbeat_at>stamp-interval '90 seconds') THEN
            RAISE EXCEPTION 'Setup renewal requires live correlated source ownership'
                USING ERRCODE='23514';
        END IF;
        NEW.renewed_at=stamp;
    END IF;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION public.stewardship_setup_install_selected_v1(ready_id uuid, credential_id uuid, credential_version bigint, fingerprint text) RETURNS boolean
    LANGUAGE sql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $_$
    SELECT EXISTS (
        SELECT 1 FROM public.stewardship_setup_readiness_binding ready
        JOIN public.stewardship_setup_config_intent intent ON intent.id=ready.intent_id
        JOIN public.stewardship_setup_sealed_credential credential
            ON credential.id=$2 AND credential.attempt_id=intent.attempt_id
            AND credential.version=$3 AND credential.fingerprint=$4
            AND credential.scrubbed_at IS NULL
        WHERE ready.id=$1 AND CASE credential.target
            -- The parish data check (#2201), or the staged load a binding
            -- made before it holds.
            WHEN 'parishsoft' THEN EXISTS (
                SELECT 1 FROM public.stewardship_setup_source_check checked
                WHERE checked.id=ready.source_check_id AND checked.credential_id=$2
                    AND checked.credential_version=$3 AND checked.fingerprint=$4)
                OR EXISTS (
                SELECT 1 FROM public.stewardship_setup_source_result result
                JOIN public.stewardship_setup_source_exchange exchange ON exchange.id=result.exchange_id
                WHERE result.id=ready.source_result_id AND exchange.credential_id=$2
                    AND exchange.credential_version=$3 AND exchange.fingerprint=$4)
            WHEN 'google_workspace' THEN EXISTS (
                SELECT 1 FROM public.stewardship_setup_mail_delivery delivery
                WHERE delivery.id=ready.mail_delivery_id AND delivery.credential_id=$2
                    AND delivery.credential_version=$3 AND delivery.fingerprint=$4
                    AND delivery.state='accepted')
            WHEN 'slack' THEN EXISTS (
                SELECT 1 FROM public.stewardship_setup_slack_delivery delivery
                WHERE delivery.id=ready.slack_delivery_id AND delivery.credential_id=$2
                    AND delivery.credential_version=$3 AND delivery.fingerprint=$4
                    AND delivery.state='accepted')
            ELSE false END
    );
$_$;

DO $check$
DECLARE missing text;
BEGIN
    IF to_regclass('public.stewardship_setup_source_check') IS NULL
       OR to_regclass('public.setup_check_one_waiting') IS NULL THEN
        RAISE EXCEPTION 'Migration 0183: the parish data check table is not installed as declared';
    END IF;
    -- Every new function, with its declared rights and fixed search path,
    -- and no EXECUTE for PUBLIC.
    SELECT string_agg(name, ', ') INTO missing FROM (VALUES
        ('stewardship_setup_source_check_mutable_v1()', false),
        ('stewardship_setup_source_check_guard_v1()', true),
        ('stewardship_setup_discard_checks_v1()', true),
        ('stewardship_setup_freeze_check_v1()', true)
    ) expected(name, definer)
    WHERE NOT EXISTS (SELECT 1 FROM pg_proc p
        WHERE p.oid=to_regprocedure('public.'||expected.name)
          AND p.prosecdef=expected.definer
          AND p.proconfig OPERATOR(pg_catalog.@>) ARRAY['search_path=pg_catalog, public, pg_temp']
          AND NOT has_function_privilege('public', p.oid, 'EXECUTE'));
    IF missing IS NOT NULL THEN
        RAISE EXCEPTION 'Migration 0183: not installed as declared: %', missing;
    END IF;
    -- The guard holds both web writes to the owner's live sign-in.
    IF (SELECT count(*) FROM regexp_matches(
            (SELECT prosrc FROM pg_proc WHERE oid=to_regprocedure(
                'public.stewardship_setup_source_check_guard_v1()')),
            'public\.stewardship_setup_owner_live_v1\(NEW\.attempt_id\)', 'g'))<>2 THEN
        RAISE EXCEPTION 'Migration 0183: the parish data check guard is not installed as declared';
    END IF;
    -- Each trigger is in place and enabled.
    SELECT string_agg(name, ', ') INTO missing FROM (VALUES
        ('stewardship_setup_source_check', 'stewardship_setup_source_check_mutable_guard_v1',
            'stewardship_setup_source_check_mutable_v1()'),
        ('stewardship_setup_source_check', 'stewardship_setup_source_check_guard',
            'stewardship_setup_source_check_guard_v1()'),
        ('stewardship_setup_attempt', 'stewardship_setup_discard_checks',
            'stewardship_setup_discard_checks_v1()'),
        ('stewardship_setup_attempt', 'stewardship_setup_freeze_check',
            'stewardship_setup_freeze_check_v1()')
    ) expected(relation, name, function)
    WHERE NOT EXISTS (SELECT 1 FROM pg_trigger t
        WHERE t.tgrelid=to_regclass('public.'||expected.relation) AND t.tgname=expected.name
          AND t.tgfoid=to_regprocedure('public.'||expected.function)
          AND NOT t.tgisinternal AND t.tgenabled='O');
    IF missing IS NOT NULL THEN
        RAISE EXCEPTION 'Migration 0183: a parish data check trigger is missing: %', missing;
    END IF;
    -- The readiness record binds the check, or a staged load made before.
    IF NOT EXISTS (SELECT 1 FROM pg_attribute a
            WHERE a.attrelid='public.stewardship_setup_readiness_binding'::regclass
              AND a.attname='source_check_id' AND NOT a.attnotnull AND NOT a.attisdropped)
       OR NOT EXISTS (SELECT 1 FROM pg_attribute a
            WHERE a.attrelid='public.stewardship_setup_readiness_binding'::regclass
              AND a.attname='source_result_id' AND NOT a.attnotnull AND NOT a.attisdropped)
       OR NOT EXISTS (SELECT 1 FROM pg_constraint c
            WHERE c.conrelid='public.stewardship_setup_readiness_binding'::regclass
              AND c.conname='setup_readiness_one_source' AND c.contype='c')
       OR NOT EXISTS (SELECT 1 FROM pg_constraint c
            WHERE c.conrelid='public.stewardship_setup_readiness_binding'::regclass
              AND c.contype='f'
              AND c.confrelid='public.stewardship_setup_source_check'::regclass) THEN
        RAISE EXCEPTION 'Migration 0183: the readiness record does not bind the parish data check';
    END IF;
    -- The replaced functions carry this change and keep their fixed search
    -- path and their caller's rights.
    SELECT string_agg(name, ', ') INTO missing FROM (VALUES
        ('stewardship_setup_readiness_guard_v1()',
            'Setup readiness source is not the current parish data check'),
        ('stewardship_setup_readiness_guard_v1()',
            'IF NEW.source_result_id IS NOT NULL OR NOT EXISTS ('),
        ('stewardship_setup_attempt_guard_v1()',
            'AND (NEW.source_task_id IS NULL OR task.state IN (''succeeded'',''failed'')))) IS NOT TRUE THEN'),
        ('stewardship_setup_install_selected_v1(uuid,uuid,bigint,text)',
            'WHERE checked.id=ready.source_check_id AND checked.credential_id=$2')
    ) expected(name, marker)
    WHERE NOT EXISTS (SELECT 1 FROM pg_proc p
        WHERE p.oid=to_regprocedure('public.'||expected.name)
          AND strpos(p.prosrc, expected.marker)>0 AND NOT p.prosecdef
          AND p.proconfig OPERATOR(pg_catalog.@>) ARRAY['search_path=pg_catalog, public, pg_temp']);
    IF missing IS NOT NULL THEN
        RAISE EXCEPTION 'Migration 0183: a replaced function lacks the parish data check: %', missing;
    END IF;
    IF current_setting('stewardship.migration_0183_acl', true) IS DISTINCT FROM (
           SELECT string_agg(p.proname||'='||coalesce(p.proacl::text,'default'), ';' ORDER BY p.proname)
           FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
           WHERE n.nspname='public'
             AND p.proname IN ('stewardship_setup_readiness_guard_v1','stewardship_setup_attempt_guard_v1',
                 'stewardship_setup_install_selected_v1')) THEN
        RAISE EXCEPTION 'Migration 0183: a replaced function lost or changed its grants';
    END IF;
END
$check$;
