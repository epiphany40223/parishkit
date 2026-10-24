-- Frozen forward migration file 0174: the setup wizard's ParishSoft
-- organization choice (#1554, admin-portal spec "ParishSoft organization
-- choice"). This file is installed by its Django migration in the accounts
-- app and must never change once released;
-- tests/stewardship/test_schema_migration_files.py pins its digest.
-- Reversing needs its own forward migration.
--
-- The ParishSoft connection step asks only for the API key. The web seals
-- the key with no settings and queues an organization lookup, a new
-- stewardship_setup_parishsoft_lookup row bound to that key's staged
-- version and fingerprint. The isolated ParishSoft installer asks
-- ParishSoft which organizations the key reaches and whether each holds
-- Families, and records the bounded list (or a stable error code); the
-- Administrator then chooses one, which the web writes into the staged
-- key's settings as {"organization_id": N}, exactly what the removed
-- organization ID field wrote before. The lookup's guard admits each move
-- for its one owner and only under the work lock: the web's request,
-- choice and discard (the choice only of a listed organization that is not
-- known to hold no Families, its name copied from the list), and the
-- installer's claim and answer. Choosing, discarding or ending setup
-- scrubs the list, so the other organizations' IDs and names are never
-- kept; the choice records setup_parishsoft_organization_chosen with
-- counts only. The guard and the end-of-setup scrub are SECURITY DEFINER,
-- stated again with ALTER as every definer is; the other new functions are
-- invoker's rights.
--
-- This file replaces, each its baseline body with only this change (their
-- final bodies are also the baseline's):
--   stewardship_setup_secret_guard_v1  admits a ParishSoft key staged with
--     no settings while its lookup runs, and lets the web discard such a
--     key for the attempt's owner while collecting (No or Cancel)
--   stewardship_safe_context_v1        four integer action keys for the
--     choice's audit context
-- Both stay SECURITY INVOKER with their pinned search_path, and CREATE OR
-- REPLACE keeps their grants. A new trigger refuses to start the setup
-- load while the staged key has no chosen organization or a lookup is
-- still open. Table grants are runtime_grants'
-- (accounts/setup_lookup_grants.py). The file ends with a DO block that
-- refuses to commit unless all of it is installed as declared.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

-- The two replaced functions' grants before the replacement, compared in
-- the DO block. A transaction-local setting holds them, not a temporary
-- table: the migration login has no TEMP privilege.
SELECT set_config('stewardship.migration_0174_acl', (
    SELECT string_agg(p.proname||'='||coalesce(p.proacl::text,'default'), ';' ORDER BY p.proname)
    FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
    WHERE n.nspname='public'
      AND p.proname IN ('stewardship_safe_context_v1','stewardship_setup_secret_guard_v1')), true);

-- The lookup table (accounts/setup_lookup_models.SetupParishSoftLookup).
CREATE TABLE "stewardship_setup_parishsoft_lookup" ("id" uuid NOT NULL PRIMARY KEY, "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "actor_id" uuid NULL, "correlation_id" uuid NOT NULL, "updated_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "version" bigint NOT NULL CHECK ("version" >= 0), "credential_version" bigint NOT NULL CHECK ("credential_version" >= 0), "fingerprint" varchar(64) NOT NULL, "state" varchar(16) NOT NULL, "error_code" varchar(48) NULL, "organizations" jsonb NULL, "chosen_id" bigint NULL CHECK ("chosen_id" >= 0), "chosen_name" varchar(200) NULL, "worker_id" uuid NULL, "started_at" timestamp with time zone NULL, "deadline_at" timestamp with time zone NULL, "finished_at" timestamp with time zone NULL, "scrubbed_at" timestamp with time zone NULL, "attempt_id" uuid NOT NULL, "credential_id" uuid NOT NULL, CONSTRAINT "stewardship_accounts_setupparishsoftlookup_positive_version" CHECK ("version" >= 1), CONSTRAINT "setup_lookup_known_state" CHECK (((state)::text = ANY (ARRAY[('queued'::character varying)::text, ('running'::character varying)::text, ('ready'::character varying)::text, ('failed'::character varying)::text, ('chosen'::character varying)::text, ('discarded'::character varying)::text]))), CONSTRAINT "setup_lookup_binding_shape" CHECK (("credential_version" >= 1 AND "fingerprint"::text ~ '^[0-9a-f]{64}$')), CONSTRAINT "setup_lookup_error_shape" CHECK (((error_code IS NULL) OR (((error_code)::text = ANY (ARRAY[('parishsoft_key_refused'::character varying)::text, ('parishsoft_no_organizations'::character varying)::text, ('parishsoft_no_family_organizations'::character varying)::text, ('parishsoft_organizations_unreadable'::character varying)::text, ('parishsoft_unavailable'::character varying)::text])) AND ((state)::text = 'failed'::text)))), CONSTRAINT "setup_lookup_list_shape" CHECK (("organizations" IS NULL OR "state" = 'ready')), CONSTRAINT "setup_lookup_choice_shape" CHECK ((("chosen_id" >= 1 AND "chosen_id" < 2147483648 AND "state" = 'chosen') OR ("chosen_id" IS NULL AND "chosen_name" IS NULL))), CONSTRAINT "setup_lookup_scrub_shape" CHECK ((((scrubbed_at IS NOT NULL) AND ((state)::text = ANY (ARRAY[('chosen'::character varying)::text, ('discarded'::character varying)::text]))) OR ((scrubbed_at IS NULL) AND ((state)::text = ANY (ARRAY[('queued'::character varying)::text, ('running'::character varying)::text, ('ready'::character varying)::text, ('failed'::character varying)::text]))))));
ALTER TABLE "stewardship_setup_parishsoft_lookup" ADD CONSTRAINT "stewardship_setup_pa_attempt_id_4a14823f_fk_stewardsh" FOREIGN KEY ("attempt_id") REFERENCES "stewardship_setup_attempt" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_setup_parishsoft_lookup" ADD CONSTRAINT "stewardship_setup_pa_credential_id_c8694b5c_fk_stewardsh" FOREIGN KEY ("credential_id") REFERENCES "stewardship_setup_sealed_credential" ("id") DEFERRABLE INITIALLY DEFERRED;
CREATE UNIQUE INDEX "setup_lookup_one_open" ON "stewardship_setup_parishsoft_lookup" ("attempt_id") WHERE ((state)::text = ANY (ARRAY[('queued'::character varying)::text, ('running'::character varying)::text, ('ready'::character varying)::text, ('failed'::character varying)::text]));
CREATE INDEX "stewardship_setup_parishsoft_lookup_correlation_id_3d5bea03" ON "stewardship_setup_parishsoft_lookup" ("correlation_id");
CREATE INDEX "stewardship_setup_parishsoft_lookup_attempt_id_4a14823f" ON "stewardship_setup_parishsoft_lookup" ("attempt_id");
CREATE INDEX "stewardship_setup_parishsoft_lookup_credential_id_c8694b5c" ON "stewardship_setup_parishsoft_lookup" ("credential_id");

-- Every update advances the version and keeps the identity and bindings,
-- in the quoted per-column form every generated mutable guard uses, which
-- test_all_concrete_mutable_records_have_enabled_guard checks against the
-- model's immutable and write-once fields.
CREATE FUNCTION public.stewardship_setup_parishsoft_lookup_mutable_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF NEW."id" IS DISTINCT FROM OLD."id"
       OR NEW."created_at" IS DISTINCT FROM OLD."created_at"
       OR NEW."attempt_id" IS DISTINCT FROM OLD."attempt_id"
       OR NEW."credential_id" IS DISTINCT FROM OLD."credential_id"
       OR NEW."credential_version" IS DISTINCT FROM OLD."credential_version"
       OR NEW."fingerprint" IS DISTINCT FROM OLD."fingerprint"
       OR (OLD."worker_id" IS NOT NULL AND NEW."worker_id" IS DISTINCT FROM OLD."worker_id")
       OR (OLD."started_at" IS NOT NULL AND NEW."started_at" IS DISTINCT FROM OLD."started_at")
       OR (OLD."deadline_at" IS NOT NULL AND NEW."deadline_at" IS DISTINCT FROM OLD."deadline_at")
       OR (OLD."finished_at" IS NOT NULL AND NEW."finished_at" IS DISTINCT FROM OLD."finished_at")
       OR (OLD."scrubbed_at" IS NOT NULL AND NEW."scrubbed_at" IS DISTINCT FROM OLD."scrubbed_at") THEN
        RAISE EXCEPTION 'Record identity and bindings are immutable' USING ERRCODE='23514';
    END IF;
    IF NEW.version IS DISTINCT FROM OLD.version + 1 THEN
        RAISE EXCEPTION 'Every update must advance the record version' USING ERRCODE='23514';
    END IF;
    NEW.updated_at:=statement_timestamp();
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.stewardship_setup_parishsoft_lookup_mutable_v1() FROM PUBLIC;
CREATE TRIGGER stewardship_setup_parishsoft_lookup_mutable_guard_v1 BEFORE UPDATE
    ON public.stewardship_setup_parishsoft_lookup
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_parishsoft_lookup_mutable_v1();

-- Whether an installer's answer is a well-formed organization list: 1 to
-- 10,000 objects of exactly id (a positive 32-bit integer, none repeated),
-- name (text of 1 to 200 characters, or null) and families (true, false
-- or null: unknown), at least one not known to hold no Families (a key
-- whose organizations all hold none fails the lookup instead).
CREATE FUNCTION public.stewardship_setup_lookup_list_valid_v1(list jsonb) RETURNS boolean
LANGUAGE sql IMMUTABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT jsonb_typeof(list)='array'
       AND jsonb_array_length(list) BETWEEN 1 AND 10000
       AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(list) o
           WHERE jsonb_typeof(o)<>'object'
              OR (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(o) k)
                  IS DISTINCT FROM ARRAY['families','id','name']
              OR jsonb_typeof(o->'id')<>'number'
              -- Parenthesized: the cast runs only on a well-formed ID.
              OR (CASE WHEN (o->>'id') ~ '^[1-9][0-9]{0,9}$'
                  THEN (o->>'id')::numeric>=2147483648 ELSE true END)
              OR jsonb_typeof(o->'name') NOT IN ('string','null')
              OR (jsonb_typeof(o->'name')='string'
                  AND length(o->>'name') NOT BETWEEN 1 AND 200)
              OR jsonb_typeof(o->'families') NOT IN ('boolean','null'))
       AND (SELECT count(DISTINCT o->>'id') FROM jsonb_array_elements(list) o)
           =jsonb_array_length(list)
       AND EXISTS (SELECT 1 FROM jsonb_array_elements(list) o
           WHERE o->'families' IS DISTINCT FROM 'false'::jsonb)
$$;

-- The lookup's guard, with owner rights: the installer reads no audit
-- table and the end-of-setup scrub runs for whichever login ends setup.
-- Every write needs the work lock. INSERT: only the web login, for the
-- attempt's owner while it is collecting, bound to its unscrubbed staged
-- ParishSoft key's exact version and fingerprint, queued and empty.
-- UPDATE of an open lookup: by the ParishSoft installer, queued to running
-- (its claim) and running to ready (a valid list) or failed (an error
-- code); by the web, for the owner while collecting, ready to chosen (a
-- listed organization not known to hold no Families, with its listed name,
-- while the key is still the one looked up) or any open state to
-- discarded; and, once setup has ended, the scrub to discarded. Choosing
-- or discarding scrubs the list. A finished lookup never changes. DELETE:
-- never.
CREATE FUNCTION public.stewardship_setup_parishsoft_lookup_guard_v1() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE attempt public.stewardship_setup_attempt%ROWTYPE;
    credential public.stewardship_setup_sealed_credential%ROWTYPE;
    choice jsonb; event_id uuid; stamp timestamptz:=clock_timestamp();
BEGIN
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'ParishSoft organization lookups are retained' USING ERRCODE='23514';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
        AND mode='ExclusiveLock' AND granted) THEN
        RAISE EXCEPTION 'ParishSoft organization lookups require ordered ownership'
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
           OR NEW.error_code IS NOT NULL OR NEW.organizations IS NOT NULL
           OR NEW.chosen_id IS NOT NULL OR NEW.chosen_name IS NOT NULL
           OR NEW.worker_id IS NOT NULL OR NEW.started_at IS NOT NULL
           OR NEW.deadline_at IS NOT NULL OR NEW.finished_at IS NOT NULL
           OR NEW.scrubbed_at IS NOT NULL THEN
            RAISE EXCEPTION 'A ParishSoft organization lookup needs its setup''s staged key and Administrator'
                USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    IF OLD.state NOT IN ('queued','running','ready','failed') THEN
        RAISE EXCEPTION 'A finished ParishSoft organization lookup cannot change'
            USING ERRCODE='23514';
    END IF;
    IF attempt.state IN ('expired','completed') THEN
        -- The end-of-setup scrub (stewardship_setup_scrub_lookups_v1).
        IF NEW.state<>'discarded' OR NEW.organizations IS NOT NULL
           OR NEW.error_code IS NOT NULL OR NEW.chosen_id IS NOT NULL
           OR NEW.chosen_name IS NOT NULL OR NEW.scrubbed_at IS NULL
           OR NEW.actor_id IS NOT NULL THEN
            RAISE EXCEPTION 'An ended setup only scrubs its ParishSoft organization lookup'
                USING ERRCODE='23514';
        END IF;
        NEW.scrubbed_at:=stamp;
        RETURN NEW;
    END IF;
    IF attempt.state IS DISTINCT FROM 'collecting' THEN
        RAISE EXCEPTION 'A ParishSoft organization lookup changes only while setup collects'
            USING ERRCODE='23514';
    END IF;
    IF session_user='pk_stewardship_credential_parishsoft' THEN
        IF NEW.actor_id IS NOT NULL OR NEW.chosen_id IS NOT NULL
           OR NEW.chosen_name IS NOT NULL OR NEW.scrubbed_at IS NOT NULL THEN
            RAISE EXCEPTION 'The ParishSoft installer only answers an organization lookup'
                USING ERRCODE='23514';
        ELSIF OLD.state='queued' AND NEW.state='running' THEN
            IF NEW.worker_id IS NULL OR NEW.started_at IS NULL
               OR NEW.deadline_at IS NULL OR NEW.deadline_at<=NEW.started_at
               OR NEW.finished_at IS NOT NULL OR NEW.organizations IS NOT NULL
               OR NEW.error_code IS NOT NULL
               OR credential.scrubbed_at IS NOT NULL
               OR credential.version IS DISTINCT FROM NEW.credential_version
               OR credential.fingerprint IS DISTINCT FROM NEW.fingerprint THEN
                RAISE EXCEPTION 'An organization lookup is claimed only for its current staged key'
                    USING ERRCODE='23514';
            END IF;
        ELSIF OLD.state='running' AND NEW.state='ready' THEN
            IF NEW.finished_at IS NULL OR NEW.error_code IS NOT NULL
               OR NEW.organizations IS NULL
               OR NOT public.stewardship_setup_lookup_list_valid_v1(NEW.organizations) THEN
                RAISE EXCEPTION 'An organization lookup answer must be a valid organization list'
                    USING ERRCODE='23514';
            END IF;
        ELSIF OLD.state='running' AND NEW.state='failed' THEN
            IF NEW.finished_at IS NULL OR NEW.error_code IS NULL
               OR NEW.organizations IS NOT NULL THEN
                RAISE EXCEPTION 'A failed organization lookup keeps only its error code'
                    USING ERRCODE='23514';
            END IF;
        ELSE
            RAISE EXCEPTION 'Invalid organization lookup transition' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    IF session_user<>'pk_stewardship_web' OR NEW.actor_id IS DISTINCT FROM attempt.owner_id
       OR NEW.organizations IS NOT NULL OR NEW.error_code IS NOT NULL
       OR NEW.scrubbed_at IS NULL THEN
        RAISE EXCEPTION 'Only the setup''s Administrator chooses or discards an organization lookup'
            USING ERRCODE='23514';
    END IF;
    IF NEW.state='chosen' THEN
        SELECT o INTO choice FROM jsonb_array_elements(OLD.organizations) o
            WHERE (o->>'id')::bigint=NEW.chosen_id;
        IF OLD.state<>'ready' OR choice IS NULL
           OR choice->'families'='false'::jsonb
           OR NEW.chosen_name IS DISTINCT FROM (choice->>'name')
           OR credential.scrubbed_at IS NOT NULL
           OR credential.version IS DISTINCT FROM NEW.credential_version
           OR credential.fingerprint IS DISTINCT FROM NEW.fingerprint THEN
            RAISE EXCEPTION 'Choose one of the listed ParishSoft organizations that has Families'
                USING ERRCODE='23514';
        END IF;
        -- Counts only: never another organization's ID or name.
        event_id:=gen_random_uuid();
        INSERT INTO public.stewardship_audit_event(
            id,created_at,actor_id,correlation_id,event_type,subject_id,ownership_scope)
        VALUES(event_id,stamp,NEW.actor_id,NEW.correlation_id,
            'setup_parishsoft_organization_chosen',NEW.id,'deployment');
        INSERT INTO public.stewardship_audit_context(
            id,created_at,actor_id,correlation_id,event_id,actor_kind,schema,context)
        VALUES(gen_random_uuid(),stamp,NEW.actor_id,NEW.correlation_id,event_id,
            'portal_user','action',jsonb_build_object(
                'organization',NEW.chosen_id,
                'organizations_reached',jsonb_array_length(OLD.organizations),
                'organizations_without_families',(SELECT count(*) FROM jsonb_array_elements(OLD.organizations) o
                    WHERE o->'families'='false'::jsonb),
                'organizations_unknown',(SELECT count(*) FROM jsonb_array_elements(OLD.organizations) o
                    WHERE o->'families'='null'::jsonb)));
    ELSIF NEW.state<>'discarded' OR NEW.chosen_id IS NOT NULL OR NEW.chosen_name IS NOT NULL THEN
        RAISE EXCEPTION 'Invalid organization lookup transition' USING ERRCODE='23514';
    END IF;
    NEW.scrubbed_at:=stamp;
    RETURN NEW;
END $$;
ALTER FUNCTION public.stewardship_setup_parishsoft_lookup_guard_v1() SECURITY DEFINER;
REVOKE ALL ON FUNCTION public.stewardship_setup_parishsoft_lookup_guard_v1() FROM PUBLIC;
CREATE TRIGGER stewardship_setup_parishsoft_lookup_guard BEFORE INSERT OR UPDATE OR DELETE
    ON public.stewardship_setup_parishsoft_lookup
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_parishsoft_lookup_guard_v1();

-- Ending setup scrubs every open lookup's list, with owner rights, as the
-- staged credentials are scrubbed (stewardship_setup_scrub_secrets_v1).
CREATE FUNCTION public.stewardship_setup_scrub_lookups_v1() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF NEW.state IN ('expired','completed') AND NEW.state<>OLD.state THEN
        UPDATE public.stewardship_setup_parishsoft_lookup SET state='discarded',
            organizations=NULL, error_code=NULL, scrubbed_at=clock_timestamp(),
            actor_id=NULL, correlation_id=NEW.correlation_id, version=version+1
            WHERE attempt_id=NEW.id AND state IN ('queued','running','ready','failed');
    END IF;
    RETURN NULL;
END $$;
ALTER FUNCTION public.stewardship_setup_scrub_lookups_v1() SECURITY DEFINER;
REVOKE ALL ON FUNCTION public.stewardship_setup_scrub_lookups_v1() FROM PUBLIC;
CREATE TRIGGER stewardship_setup_scrub_lookups AFTER UPDATE
    ON public.stewardship_setup_attempt
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_scrub_lookups_v1();

-- The setup load never starts for a staged ParishSoft key with no chosen
-- organization, nor while an organization lookup is still open. (The web
-- also requires a staged key before it queues the load at all.)
CREATE FUNCTION public.stewardship_setup_load_organization_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF NEW.state='loading' AND OLD.state IS DISTINCT FROM 'loading' AND (
        EXISTS (SELECT 1 FROM public.stewardship_setup_sealed_credential c
            WHERE c.attempt_id=NEW.id AND c.target='parishsoft' AND c.scrubbed_at IS NULL
              AND NOT c.settings ? 'organization_id')
        OR EXISTS (SELECT 1 FROM public.stewardship_setup_parishsoft_lookup l
            WHERE l.attempt_id=NEW.id AND l.state IN ('queued','running','ready','failed'))) THEN
        RAISE EXCEPTION 'The setup load needs a chosen ParishSoft organization (#1554)'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.stewardship_setup_load_organization_v1() FROM PUBLIC;
CREATE TRIGGER stewardship_setup_load_organization BEFORE UPDATE
    ON public.stewardship_setup_attempt
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_load_organization_v1();

-- stewardship_setup_secret_guard_v1: the baseline body, admitting a
-- ParishSoft key staged with no settings and its discard (#1554).
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
        -- A ParishSoft key whose organization is not chosen yet is
        -- discarded at once when the Administrator answers No or Cancel to
        -- its organization lookup (#1554): only the web, for the attempt's
        -- owner, while it is collecting. A key with a chosen organization
        -- is replaced, never discarded, until setup ends.
        IF attempt.state='collecting' AND NEW.target='parishsoft' THEN
            IF current_user<>'pk_stewardship_web'
               OR NEW.actor_id IS DISTINCT FROM attempt.owner_id
               OR OLD.settings<>'{}'::jsonb THEN
                RAISE EXCEPTION 'Only a ParishSoft key with no organization chosen is discarded during setup (#1554)'
                    USING ERRCODE='23514';
            END IF;
        -- Turning Slack off during setup discards the staged Slack token at
        -- once (#1499): only the web, for the attempt's owner, while it is
        -- collecting and its saved Slack step is off. Every other scrub
        -- needs the terminal fence, by the system.
        ELSIF attempt.state='collecting' THEN
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
            -- Any sign-in age (#1547); a command session still needs
            -- its live full-scope automation session (ADM-11).
            AND login.authenticated_at<=stamp
            AND (NOT EXISTS (SELECT 1 FROM public.stewardship_automation_login command
                     WHERE command.portal_session_id=login.id)
                 OR public.stewardship_automation_fresh_v1(login.id,attempt.owner_id))
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
        -- No settings while its organization lookup runs (#1554), else
        -- exactly the chosen organization.
        IF keys IS NOT NULL AND (keys IS DISTINCT FROM ARRAY['organization_id']
           OR jsonb_typeof(NEW.settings->'organization_id') <> 'number'
           OR (NEW.settings->>'organization_id') !~ '^[1-9][0-9]{0,9}$'
           OR (NEW.settings->>'organization_id')::numeric >= 2147483648) THEN
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

-- stewardship_safe_context_v1: the baseline body (0122's) with the four
-- organization choice keys.
CREATE OR REPLACE FUNCTION public.stewardship_safe_context_v1(schema_name text, payload jsonb) RETURNS boolean
    LANGUAGE plpgsql IMMUTABLE
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $_$
DECLARE allowed text[]; key text; value jsonb; text_value text;
        ministry jsonb; previous_ministry bigint; item jsonb;
BEGIN
    allowed=CASE schema_name
        WHEN 'request' THEN ARRAY['method','status','outcome','source_fingerprint']
        WHEN 'task' THEN ARRAY['task_id','count','version','outcome']
        WHEN 'email' THEN ARRAY['message_id','recipient_count','outcome','reason']
        WHEN 'source' THEN ARRAY['snapshot_id','generation','count','outcome']
        WHEN 'member_source' THEN ARRAY['family_duid','member_duid','field']
        WHEN 'provider' THEN ARRAY['status','provider_fingerprint','outcome']
        WHEN 'exception' THEN ARRAY['outcome','retryable']
        WHEN 'action' THEN ARRAY['version','before_version','after_version','outcome','source_fingerprint','candidate_fingerprint','count',
            'matching_count','page','directory_reason','directory_phone','directory_response','directory_sort','directory_reach','search_used','exact_code_used','ministry_duid','ministry_duids','ministry_operational',
            'previous_ministry_duids','added_ministry_duids','removed_ministry_duids',
            'decision','review_reason','file_slug','previous_file_slug','file_kind','file_size','file_fingerprint',
            'report_mode','report_filter','talent_option_id','snapshot_id',
            'report_sort','directory_data_check','directory_response_columns',
            'archive_year','archive_days','archive_family_rows','void_reason','task_state',
            -- How old the Google sign-in was when a setup credential was
            -- sealed, in whole seconds (#1402; audit.schemas).
            'sign_in_age_seconds',
            -- An export's report kind and file format (#1398), so the audit
            -- log answers who exported what without its request row.
            'report','format',
            -- A report view the open page made by itself to bring newer
            -- figures in (#1620; audit.schemas): a boolean.
            'automatic',
            -- The setup wizard's ParishSoft organization choice (#1554;
            -- audit.schemas): the chosen organization ID and how many
            -- organizations the key reached, held no Families or were
            -- unknown, integers as every unlisted key.
            'organization','organizations_reached','organizations_without_families',
            'organizations_unknown']
        WHEN 'boundary' THEN ARRAY['occurrence_id','kind','intended_unix_microseconds','actual_unix_microseconds','lag_microseconds','before_state','after_state']
        WHEN 'schedule' THEN ARRAY['definition_id','previous_revision_id','selected_revision_id','cancelled_messages','skipped_occurrences','failed_occurrences','delivered_slots']
        WHEN 'timeout' THEN ARRAY['task_id','task_type','attempt','limit_seconds','elapsed_seconds','what','helper','count','outcome']
        -- What made scheduled work late (#634): the task type, how many tasks
        -- and how late against which limit, or the Family send that stalled
        -- or overran, with its counts, how long since its last progress and
        -- how many other tasks were late in the same check.
        WHEN 'due_work' THEN ARRAY['task_type','count','lag_seconds','limit_seconds','definition_id','revision_id',
            'remaining_count','done_count','stall_seconds','elapsed_seconds','other_late_count',
            'occurrence_id','task_id']
        -- What failed and what happens next (#633): a closed word for what
        -- failed, the exception's category, the task, message and attempt,
        -- a closed provider reason or HTTP status, and whether it retries.
        WHEN 'failure' THEN ARRAY['failure','failure_kind','task_id','task_type','message_id','version',
            'attempt','attempt_limit','retry_seconds','status','reason','count','outcome','command']
        -- An operational incident that ended (#633): the incident, its kind,
        -- the entry that opened it, how long it lasted and how often it was seen.
        WHEN 'recovery' THEN ARRAY['incident_id','incident_kind','log_id','elapsed_seconds','count']
        -- A refused Admin input (#1403): its page or command, server-owned
        -- form and field names, error codes and the reason, never what was
        -- submitted (audit.refusal_context).
        WHEN 'refusal' THEN ARRAY['route','command','method','status','form','fields','codes',
            'reason','exception_class','expected','actual','unexpected_count','mode','capped']
        ELSE NULL END;
    IF allowed IS NULL OR jsonb_typeof(payload) IS DISTINCT FROM 'object' THEN RETURN false; END IF;
    IF schema_name IN ('member_source','boundary') AND NOT payload ?& allowed THEN RETURN false; END IF;
    -- A refusal always says why and names exactly one of its page and its
    -- command; an exception class and an unexpected-field count belong
    -- only to their own reasons.
    IF schema_name='refusal' AND (NOT payload ? 'reason'
        OR (payload ? 'route')=(payload ? 'command')
        OR (payload ? 'exception_class' AND payload->>'reason' IS DISTINCT FROM 'unclassified')
        OR (payload ? 'unexpected_count' AND payload->>'reason' IS DISTINCT FROM 'unexpected_fields')) THEN
        RETURN false;
    END IF;
    FOR key,value IN SELECT * FROM jsonb_each(payload) LOOP
        IF NOT key=ANY(allowed) THEN RETURN false; END IF;
        text_value=value#>>'{}';
        -- A refused Admin input (#1403), checked by its own rules: these keys
        -- mean something else in the other schemas. Identifiers share one
        -- shape (Python holds their closed sets); expected and actual are
        -- setting values such as zone names, never an address.
        IF schema_name='refusal' THEN
            IF key IN ('route','method','form','reason','exception_class') THEN
                IF jsonb_typeof(value)<>'string' OR text_value!~'^[a-z][a-z0-9_:-]{0,63}$' THEN RETURN false; END IF;
            ELSIF key='command' THEN
                IF jsonb_typeof(value)<>'string' OR length(text_value)>64
                   OR text_value!~'^[a-z][a-z-]*( [a-z][a-z-]*){0,3}$' THEN RETURN false; END IF;
            ELSIF key IN ('fields','codes') THEN
                IF jsonb_typeof(value)<>'array' OR jsonb_array_length(value) NOT BETWEEN 1 AND 10 THEN RETURN false; END IF;
                FOR item IN SELECT * FROM jsonb_array_elements(value) LOOP
                    IF jsonb_typeof(item)<>'string' OR item#>>'{}'!~'^[a-z][a-z0-9_:-]{0,63}$' THEN RETURN false; END IF;
                END LOOP;
            ELSIF key IN ('expected','actual') THEN
                IF jsonb_typeof(value)<>'string' OR text_value!~'^[A-Za-z0-9_+./-]{1,64}$' THEN RETURN false; END IF;
            ELSIF key='mode' THEN
                IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('testing','production') THEN RETURN false; END IF;
            ELSIF key='capped' THEN
                IF jsonb_typeof(value)<>'boolean' THEN RETURN false; END IF;
            ELSIF key='status' THEN
                IF jsonb_typeof(value)<>'number' OR text_value NOT IN ('400','403','404','409','410','422') THEN RETURN false; END IF;
            ELSE
                IF jsonb_typeof(value)<>'number' OR text_value!~'^[0-9]{1,4}$' THEN RETURN false; END IF;
            END IF;
        ELSIF key='outcome' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('started','succeeded','denied','failed','retry','cancelled','changed') THEN RETURN false; END IF;
        ELSIF key='reason' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('no_deliverable_recipient',
                'smtp_transient','smtp_unavailable','smtp_permanent','smtp_systemic','smtp_delivery_unknown',
                'preparation_failed','slack_not_sent','slack_delivery_unknown') THEN RETURN false; END IF;
        -- What failed (#633): a closed word (audit.schemas.FAILURES).
        ELSIF key='failure' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('organization_mismatch','destructive_change',
                'shifted_scan','invalid_payload','incomplete_collection','invalid_response','lease_unavailable',
                'configuration_activating','configuration_busy','scope_changed','credential_unreadable',
                'credential_changed','provider_status','provider_timeout','provider_unreachable',
                'source_configuration','organization_changed','source_health_check','mail_health_check',
                'due_work_health_check','backup_health_check','export_cleanup','export_discard','fact_verification',
                'source_retention','family_engagement','alert_mail','security_mail','slack_alert',
                'smtp_systemic','smtp_unavailable','web_health_check','web_unresponsive',
                'admin_command','admin_command_outcome_unknown','digest_funnel_chart',
                'digest_activity_chart','digest_comparison_chart',
                'retry_refusal') THEN RETURN false; END IF;
        -- An Admin command-line command (#617): its catalog name, lower-case
        -- words such as 'export create' (Python checks the catalog itself).
        ELSIF key='command' THEN
            IF jsonb_typeof(value)<>'string' OR length(text_value)>64
               OR text_value!~'^[a-z][a-z-]*( [a-z][a-z-]*){0,3}$' THEN RETURN false; END IF;
        -- A failure's category and an incident's kind: identifier words whose
        -- closed sets Python owns (observability.FailureKind, IncidentKind).
        ELSIF key IN ('failure_kind','incident_kind') THEN
            IF jsonb_typeof(value)<>'string' OR text_value!~'^[a-z][a-z0-9_]{0,63}$' THEN RETURN false; END IF;
        ELSIF key='kind' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('start','close') THEN RETURN false; END IF;
        ELSIF key IN ('before_state','after_state') THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('draft','scheduled','active','closed','archived',
                'purging','purge_cleanup_failed','purged') THEN RETURN false; END IF;
        ELSIF key='field' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN (
                'prefix','first_name','middle_name','last_name','suffix','nickname',
                'maiden_name','birth_date','gender','email','home_phone',
                'mobile_phone','work_phone','marital_status','language','death_date'
            ) THEN RETURN false; END IF;
        ELSIF key IN ('family_duid','member_duid','ministry_duid') THEN
            IF jsonb_typeof(value)<>'number' OR text_value!~'^[0-9]{1,10}$' THEN RETURN false; END IF;
            IF text_value::numeric NOT BETWEEN 1 AND 2147483647 THEN RETURN false; END IF;
        ELSIF key IN ('ministry_duids','previous_ministry_duids','added_ministry_duids','removed_ministry_duids') THEN
            IF jsonb_typeof(value)<>'array' THEN RETURN false; END IF;
            previous_ministry:=0;
            FOR ministry IN SELECT * FROM jsonb_array_elements(value) LOOP
                IF jsonb_typeof(ministry)<>'number' OR ministry#>>'{}'!~'^[0-9]{1,10}$'
                THEN RETURN false; END IF;
                IF (ministry#>>'{}')::bigint NOT BETWEEN previous_ministry+1 AND 2147483647
                THEN RETURN false; END IF;
                previous_ministry:=(ministry#>>'{}')::bigint;
            END LOOP;
        ELSIF key='method' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('GET','HEAD','POST') THEN RETURN false; END IF;
        -- Work stopped by a time limit (#293): the task type and the limit.
        ELSIF key='task_type' THEN
            IF jsonb_typeof(value)<>'string' OR text_value!~'^[a-z][a-z0-9_]{0,63}$' THEN RETURN false; END IF;
        ELSIF key='what' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('read_guard','lease','retention_budget','drive_copy_budget',
                'drive_retry_budget','drive_request','drive_probe_wait',
                'statement_timeout','lock_timeout','transaction_timeout','mail_helper','source_helper','provider_check',
                'renewal_drain','control_lock','web_drain','web_heartbeat','configuration_activation','web_probe','source_load_budget',
                'archive_check','digest_chart') THEN RETURN false; END IF;
        ELSIF key='helper' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('readiness_delivery_worker','readiness_notification_worker',
                'family_delivery_worker','digest_delivery_worker','weekly_delivery_worker','operational_mail_worker',
                'operational_slack_worker','security_mail_worker','provider_check_worker','parishsoft_http_worker',
                'archive_check_worker','chart_render_worker') THEN RETURN false; END IF;
        -- An Administrator's decision on a suspended Chairperson seed: a closed
        -- word, and the reason entered for it, bounded text refused when it
        -- carries an address-like token, since this context holds no
        -- personal data.
        ELSIF key='decision' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('keep_role','restore','remove') THEN RETURN false; END IF;
        -- A discarded setup load (#1353): why it was discarded and how the
        -- load had ended (audit.schemas.VOID_REASONS, VOIDED_TASK_STATES).
        ELSIF key='void_reason' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('key_changed','organization_changed','reload') THEN RETURN false; END IF;
        ELSIF key='task_state' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('succeeded','failed') THEN RETURN false; END IF;
        -- An export's report kind and file format (#1398): the closed sets
        -- export_report_known admits (audit.schemas.EXPORT_REPORTS, EXPORT_FORMATS).
        ELSIF key='report' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('participation','additional_information',
                'family_directory','postal_outreach','ministry','financial','family_test_names',
                'family_timeline','campaign_comparison','campaign_archive') THEN RETURN false; END IF;
        ELSIF key='format' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('csv','xlsx','pdf','png') THEN RETURN false; END IF;
        ELSIF key='review_reason' THEN
            IF jsonb_typeof(value)<>'string' OR length(text_value) NOT BETWEEN 1 AND 500
               OR position('@' in text_value)>0 THEN RETURN false; END IF;
        -- A hosted file (#346): its placeholder name and its stored type.
        ELSIF key IN ('file_slug','previous_file_slug') THEN
            IF jsonb_typeof(value)<>'string' OR length(text_value)>64
               OR text_value!~'^[a-z0-9]+(-[a-z0-9]+)*$' THEN RETURN false; END IF;
        ELSIF key='file_kind' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('pdf','docx','xlsx','pptx','png','jpeg') THEN RETURN false; END IF;
        -- An on-request report page or download (#556): the system mode it
        -- showed and its closed filter choice (audit.schemas.REPORT_FILTERS).
        ELSIF key='report_mode' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('production','testing') THEN RETURN false; END IF;
        ELSIF key='report_filter' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('all','invited','uninvited','progressed',
                'opened','followed','unfollowed','mailing-name','envelope','any','cannot_serve',
                'cannot_attend','option') THEN RETURN false; END IF;
        -- A response list's sort order (#851): its closed sort token, a column
        -- key ascending or '-key' descending (audit.schemas.REPORT_SORTS).
        ELSIF key='report_sort' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('family','-family',
                'duid','-duid','envelope','-envelope','submitted','-submitted',
                'submissions','-submissions','opened','-opened','progressed','-progressed',
                'invited','-invited','link','-link','last','-last','mailing','-mailing',
                'problem','-problem','staff_entered','-staff_entered') THEN RETURN false; END IF;
        ELSIF key LIKE '%\_id' ESCAPE '\' THEN
            IF jsonb_typeof(value)<>'string' OR text_value!~'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' THEN RETURN false; END IF;
        ELSIF key LIKE '%\_fingerprint' ESCAPE '\' THEN
            IF jsonb_typeof(value)<>'string' OR text_value!~'^[0-9a-f]{64}$' THEN RETURN false; END IF;
        ELSIF key IN ('retryable','search_used','exact_code_used','ministry_operational','directory_response_columns','automatic') THEN
            IF jsonb_typeof(value)<>'boolean' THEN RETURN false; END IF;
        ELSIF key='directory_reason' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('any','no_head','no_address','invalid_address','provider_refused','deliverable') THEN RETURN false; END IF;
        ELSIF key='directory_phone' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('any','yes','no') THEN RETURN false; END IF;
        -- The directory's Response filter (#933): its closed choices, plus the
        -- older yes and no that earlier entries recorded.
        ELSIF key='directory_response' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('any','yes','no','submitted',
                'more-than-once','started','progressed','opened-only','never-opened',
                'link-followed','link-not-followed','not-invited','not-submitted') THEN RETURN false; END IF;
        -- The directory's ParishSoft data to check filter (#933).
        ELSIF key='directory_data_check' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('any','anything','mailing-name','envelope') THEN RETURN false; END IF;
        ELSIF key='directory_sort' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('name','name_desc','duid','duid_desc',
                'invited','invited_desc','link','link_desc','opened','opened_desc',
                'progressed','progressed_desc','submitted','submitted_desc','last','last_desc',
                'submissions','submissions_desc') THEN RETURN false; END IF;
        -- How campaign mail can reach the listed Families (#388 L1).
        ELSIF key='directory_reach' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('any','email','mail','neither') THEN RETURN false; END IF;
        ELSE
            IF jsonb_typeof(value)<>'number' OR text_value!~'^[0-9]{1,19}$' THEN RETURN false; END IF;
            IF text_value::numeric>9223372036854775807 THEN RETURN false; END IF;
            IF key='status' AND text_value::numeric NOT BETWEEN 100 AND 599 THEN RETURN false; END IF;
        END IF;
    END LOOP;
    RETURN true;
END $_$;

DO $check$
DECLARE missing text;
BEGIN
    IF to_regclass('public.stewardship_setup_parishsoft_lookup') IS NULL
       OR to_regclass('public.setup_lookup_one_open') IS NULL THEN
        RAISE EXCEPTION 'Migration 0174: the organization lookup table is not installed as declared';
    END IF;
    -- Every new function, with its declared rights and fixed search path,
    -- and no EXECUTE for PUBLIC on the trigger functions.
    SELECT string_agg(name, ', ') INTO missing FROM (VALUES
        ('stewardship_setup_parishsoft_lookup_mutable_v1()', false, true),
        ('stewardship_setup_lookup_list_valid_v1(jsonb)', false, false),
        ('stewardship_setup_parishsoft_lookup_guard_v1()', true, true),
        ('stewardship_setup_scrub_lookups_v1()', true, true),
        ('stewardship_setup_load_organization_v1()', false, true)
    ) expected(name, definer, revoked)
    WHERE NOT EXISTS (SELECT 1 FROM pg_proc p
        WHERE p.oid=to_regprocedure('public.'||expected.name)
          AND p.prosecdef=expected.definer
          AND p.proconfig @> ARRAY['search_path=pg_catalog, public, pg_temp']
          AND (NOT expected.revoked
              OR NOT has_function_privilege('public', p.oid, 'EXECUTE')));
    IF missing IS NOT NULL THEN
        RAISE EXCEPTION 'Migration 0174: not installed as declared: %', missing;
    END IF;
    -- Each trigger is in place and enabled.
    SELECT string_agg(name, ', ') INTO missing FROM (VALUES
        ('stewardship_setup_parishsoft_lookup', 'stewardship_setup_parishsoft_lookup_mutable_guard_v1'),
        ('stewardship_setup_parishsoft_lookup', 'stewardship_setup_parishsoft_lookup_guard'),
        ('stewardship_setup_attempt', 'stewardship_setup_scrub_lookups'),
        ('stewardship_setup_attempt', 'stewardship_setup_load_organization')
    ) expected(relation, name)
    WHERE NOT EXISTS (SELECT 1 FROM pg_trigger t
        WHERE t.tgrelid=to_regclass('public.'||expected.relation) AND t.tgname=expected.name
          AND NOT t.tgisinternal AND t.tgenabled='O');
    IF missing IS NOT NULL THEN
        RAISE EXCEPTION 'Migration 0174: an organization lookup trigger is missing: %', missing;
    END IF;
    -- The list check admits a valid list only.
    IF NOT public.stewardship_setup_lookup_list_valid_v1(
           '[{"id": 1234, "name": "St. Example Parish", "families": true},
             {"id": 99, "name": null, "families": null}]'::jsonb)
       OR public.stewardship_setup_lookup_list_valid_v1('[]'::jsonb)
       OR public.stewardship_setup_lookup_list_valid_v1(
           '[{"id": 1234, "name": "St. Example Parish", "families": false}]'::jsonb)
       OR public.stewardship_setup_lookup_list_valid_v1(
           '[{"id": "1234", "name": "St. Example Parish", "families": true}]'::jsonb)
       OR public.stewardship_setup_lookup_list_valid_v1(
           '[{"id": 7, "name": "A", "families": true}, {"id": 7, "name": "B", "families": true}]'::jsonb)
       OR public.stewardship_setup_lookup_list_valid_v1(
           '[{"id": 7, "name": "A", "families": true, "extra": 1}]'::jsonb) THEN
        RAISE EXCEPTION 'Migration 0174: stewardship_setup_lookup_list_valid_v1 is not installed as declared';
    END IF;
    -- The replaced functions carry this change, keep their rights and
    -- their grants.
    SELECT string_agg(name, ', ') INTO missing FROM (VALUES
        ('stewardship_setup_secret_guard_v1()', 'IF keys IS NOT NULL AND (keys IS DISTINCT FROM ARRAY[''organization_id'']'),
        ('stewardship_setup_secret_guard_v1()', 'Only a ParishSoft key with no organization chosen is discarded during setup (#1554)'),
        ('stewardship_safe_context_v1(text,jsonb)', '''organization'',''organizations_reached'',''organizations_without_families'',')
    ) expected(name, marker)
    WHERE NOT EXISTS (SELECT 1 FROM pg_proc p
        WHERE p.oid=to_regprocedure('public.'||expected.name)
          AND strpos(p.prosrc, expected.marker)>0 AND NOT p.prosecdef
          AND p.proconfig @> ARRAY['search_path=pg_catalog, public, pg_temp']);
    IF missing IS NOT NULL THEN
        RAISE EXCEPTION 'Migration 0174: a replaced function lacks the organization choice: %', missing;
    END IF;
    IF NOT public.stewardship_safe_context_v1('action',
           '{"organization": 1234, "organizations_reached": 3,
             "organizations_without_families": 1, "organizations_unknown": 0}'::jsonb)
       OR public.stewardship_safe_context_v1('action', '{"organization": "1234"}'::jsonb)
       OR public.stewardship_safe_context_v1('request', '{"organization": 1234}'::jsonb)
       OR NOT public.stewardship_safe_context_v1('action',
            '{"outcome": "succeeded", "count": 3, "automatic": true}'::jsonb)
       OR NOT public.stewardship_safe_context_v1('refusal',
            '{"route": "admin:home", "reason": "denied"}'::jsonb) THEN
        RAISE EXCEPTION 'Migration 0174: stewardship_safe_context_v1 does not admit the organization choice as declared';
    END IF;
    IF (SELECT provolatile FROM pg_proc
        WHERE oid=to_regprocedure('public.stewardship_safe_context_v1(text,jsonb)'))<>'i' THEN
        RAISE EXCEPTION 'Migration 0174: stewardship_safe_context_v1 is no longer immutable';
    END IF;
    IF current_setting('stewardship.migration_0174_acl', true) IS DISTINCT FROM (
           SELECT string_agg(p.proname||'='||coalesce(p.proacl::text,'default'), ';' ORDER BY p.proname)
           FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
           WHERE n.nspname='public'
             AND p.proname IN ('stewardship_safe_context_v1','stewardship_setup_secret_guard_v1')) THEN
        RAISE EXCEPTION 'Migration 0174: a replaced function lost or changed its grants';
    END IF;
END
$check$;
