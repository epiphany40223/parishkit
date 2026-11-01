-- Frozen forward migration file 0200 (the repository-wide file sequence):
-- verified-address sign-in's schema (#2343; data spec "Verified-address
-- match records" and "Administration user and policy"). This file is
-- installed by its Django migration in the accounts app and must never
-- change once released; tests/stewardship/test_schema_migration_files.py
-- pins its digest and checks that its copy of the baseline's portal user
-- update guard still equals schema/functions.sql. Reversing needs its own
-- forward migration.
--
-- A Google account whose sign-in address is no user's may sign in as the
-- one user whose address Google verified for that account. This file only
-- makes that representable and guarded; nothing writes a match until the
-- sign-in code that does ships, behind the deployment setting
-- alternate_address_sign_in (off by default). It:
--
--   adds stewardship_portal_user.sign_in_email (Google's email claim from
--        the latest verified sign-in), set to email on every row with a
--        version bump (no match exists yet), then NOT NULL;
--   replaces stewardship_portal_user_insert_v1 (0030): a new row's email
--        equals its sign_in_email;
--   replaces stewardship_portal_user_mutable_v1 (the baseline): the sign-in
--        address changes only with a fresh verification, as email did; email
--        returns to sign_in_email at any time, and differs from it only with
--        that fresh verification and a standing match of the account that
--        names it, confirmed less than 30 days ago. The match table is read
--        only on that path, so a login that updates portal users without
--        matching needs no grant on it. The body now pins its search_path;
--   creates stewardship_address_match (model AddressMatch), its invoker
--        mutable guard and its definer guard stewardship_address_match_guard_v1,
--        which decides every insert, re-check and end, writes the audit rows
--        and the address_matched security event, and on some ends revokes
--        the old account's sessions;
--   creates stewardship_address_in_use_v1(address, excluding), the one
--        "is this address in use" answer for the page, the command line and
--        the guard;
--   lets stewardship_policy_security_event come from a match instead of an
--        activation (activation_id nullable, address_match_id, one of the two
--        set) and adds the matched account's sign-in address and the replaced
--        account's sign-in address and last sign-in;
--   replaces stewardship_security_content_v2 (0043) to render address_matched,
--        the twin of jobs/security_content.py.
--
-- Definers tell logins apart by session_user (inside a definer current_user
-- is the owner), as 0004's and 0013's do; the schema owner passes unchecked,
-- as 0030 and the other guards let it. There is no TRUNCATE trigger: no
-- runtime login holds TRUNCATE, and pytest-django flushes model tables as
-- the owner. Columns are added only if missing, so the users-swap tests may
-- run this file again after dropping its table and functions (as 0185's
-- does). The file ends with a DO block that refuses to commit unless all of
-- it is installed as declared.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

-- The sign-in address. Every existing account acts as its own address, so
-- the backfill copies it; the old update guard admits it as an ordinary
-- versioned update (no identity claim changes).
ALTER TABLE public.stewardship_portal_user
    ADD COLUMN IF NOT EXISTS sign_in_email varchar(254) NULL;
UPDATE public.stewardship_portal_user
    SET sign_in_email = email, version = version + 1
    WHERE sign_in_email IS DISTINCT FROM email;
ALTER TABLE public.stewardship_portal_user ALTER COLUMN sign_in_email SET NOT NULL;

-- stewardship_portal_user_insert_v1, from 0030_portal_user_insert_guard.sql:
-- a new row's sign-in address is the address it acts as.
CREATE OR REPLACE FUNCTION public.stewardship_portal_user_insert_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF pg_has_role(current_user,
        (SELECT nspowner FROM pg_namespace WHERE nspname='public'),'USAGE') THEN
        RETURN NEW;
    END IF;
    IF current_user<>'pk_stewardship_web' THEN
        RAISE EXCEPTION 'Only the web login records portal users' USING ERRCODE='42501';
    END IF;
    IF NEW.verified_at < transaction_timestamp()
       OR NEW.verified_at > statement_timestamp()
       OR NEW.disabled
       OR NEW.version<>1
       OR NEW.actor_id IS NOT NULL
       OR btrim(NEW.google_subject)=''
       OR btrim(NEW.email)=''
       -- #2343: a new account acts as its own sign-in address.
       OR NEW.sign_in_email IS DISTINCT FROM NEW.email THEN
        RAISE EXCEPTION 'A portal user insert needs an enabled new row with a verification inside this transaction'
            USING ERRCODE='23514';
    END IF;
    NEW.created_at := statement_timestamp();
    NEW.updated_at := statement_timestamp();
    RETURN NEW;
END $$;

-- stewardship_portal_user_mutable_v1, from the baseline (schema/functions.sql
-- carries this same text): its rules, the sign-in address beside the other
-- identity claims, the acted-as address's match rule, and a pinned
-- search_path. Still an invoker, so no ALTER follows.
CREATE OR REPLACE FUNCTION public.stewardship_portal_user_mutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
            BEGIN
                IF NEW."id" IS DISTINCT FROM OLD."id" OR NEW."created_at" IS DISTINCT FROM OLD."created_at" OR NEW."google_subject" IS DISTINCT FROM OLD."google_subject" THEN
                    RAISE EXCEPTION 'Record identity and bindings are immutable'
                        USING ERRCODE = '23514';
                END IF;
                -- #306 M2: disabling is one-way. No path re-enables an
                -- account; a future Administrator path needs its own evidence.
                IF OLD."disabled" AND NOT NEW."disabled" THEN
                    RAISE EXCEPTION 'A disabled portal user cannot be re-enabled'
                        USING ERRCODE = '23514';
                END IF;
                -- #306 M2: the sign-in address and hosted-domain claims
                -- change only with a re-verification stamped in this same
                -- transaction, as the Google sign-in callback does;
                -- verified_at only advances. The acted-as email (#2343)
                -- needs one too, except a return to the sign-in address,
                -- which only lowers what the account may do and is how every
                -- end of a match puts the account back.
                IF ((NEW."sign_in_email", NEW."hosted_domain", NEW."verified_at")
                    IS DISTINCT FROM (OLD."sign_in_email", OLD."hosted_domain", OLD."verified_at")
                    OR (NEW."email" IS DISTINCT FROM OLD."email"
                        AND NEW."email" IS DISTINCT FROM NEW."sign_in_email"))
                   AND (NEW."verified_at" <= OLD."verified_at"
                        OR NEW."verified_at" < transaction_timestamp()
                        OR NEW."verified_at" > statement_timestamp()) THEN
                    RAISE EXCEPTION 'Identity claims change only with a fresh verification'
                        USING ERRCODE = '23514';
                END IF;
                -- #2343: the account acts as another address than its
                -- sign-in address only while a standing verified-address
                -- match of the account names that address and was confirmed
                -- less than 30 days ago. The nested IF keeps the match table
                -- out of every other update's plan, so a login that updates
                -- portal users without matching needs no grant on it.
                IF NEW."email" IS DISTINCT FROM NEW."sign_in_email"
                   AND (NEW."email", NEW."sign_in_email")
                       IS DISTINCT FROM (OLD."email", OLD."sign_in_email") THEN
                    IF NOT EXISTS (
                        SELECT 1 FROM public.stewardship_address_match m
                        WHERE m.portal_user_id = NEW."id" AND m.ended_at IS NULL
                          AND m.email = NEW."email"
                          AND m.confirmed_at > statement_timestamp() - interval '30 days') THEN
                        RAISE EXCEPTION 'An account acts as another address only through a confirmed match'
                            USING ERRCODE = '23514';
                    END IF;
                END IF;
                IF NEW.version IS DISTINCT FROM OLD.version + 1 THEN
                    RAISE EXCEPTION 'Every update must advance the record version'
                        USING ERRCODE = '23514';
                END IF;
                NEW.updated_at := statement_timestamp();
                RETURN NEW;
            END;
            $$;

-- The match records (accounts/policy_models.py AddressMatch), as Django
-- writes the model's table, with the end check's reason list in the form
-- PostgreSQL deparses it to, which the model contract test compares.
CREATE TABLE "stewardship_address_match" ("id" uuid NOT NULL PRIMARY KEY, "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "actor_id" uuid NULL, "correlation_id" uuid NOT NULL, "updated_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "version" bigint NOT NULL CHECK ("version" >= 0), "email" varchar(254) NOT NULL, "user_record_id" uuid NOT NULL, "sign_in_email" varchar(254) NOT NULL, "matched_at" timestamp with time zone NOT NULL, "confirmed_at" timestamp with time zone NOT NULL, "ended_at" timestamp with time zone NULL, "end_reason" varchar(32) NULL, "ended_by_id" uuid NULL, "ended_session_id" uuid NULL, "portal_user_id" uuid NOT NULL);
CREATE INDEX "address_match_email_time" ON "stewardship_address_match" ("email", "matched_at");
ALTER TABLE "stewardship_address_match" ADD CONSTRAINT "stewardship_accounts_addressmatch_positive_version" CHECK ("version" >= 1);
CREATE UNIQUE INDEX "address_match_one_per_account" ON "stewardship_address_match" ("portal_user_id") WHERE "ended_at" IS NULL;
CREATE UNIQUE INDEX "address_match_one_per_address" ON "stewardship_address_match" ("email") WHERE "ended_at" IS NULL;
ALTER TABLE "stewardship_address_match" ADD CONSTRAINT "address_match_normalized" CHECK (("email" = (LOWER(TRIM("email"))) AND "sign_in_email" = (LOWER(TRIM("sign_in_email")))));
ALTER TABLE "stewardship_address_match" ADD CONSTRAINT "address_match_addresses_differ" CHECK (NOT ("email" = ("sign_in_email")));
ALTER TABLE "stewardship_address_match" ADD CONSTRAINT "address_match_end" CHECK ((("end_reason" IS NULL AND "ended_at" IS NULL AND "ended_by_id" IS NULL AND "ended_session_id" IS NULL) OR ("end_reason" = 'ended_by_administrator' AND "end_reason" IS NOT NULL AND "ended_at" IS NOT NULL AND "ended_by_id" IS NOT NULL AND "ended_session_id" IS NOT NULL) OR ((end_reason)::text = ANY ((ARRAY['sign_in_address_matches'::character varying, 'not_confirmed'::character varying, 'user_gone'::character varying, 'replaced'::character varying])::text[]) AND "end_reason" IS NOT NULL AND "ended_at" IS NOT NULL AND "ended_by_id" IS NULL AND "ended_session_id" IS NULL)));
ALTER TABLE "stewardship_address_match" ADD CONSTRAINT "stewardship_address__portal_user_id_bb46f4f3_fk_stewardsh" FOREIGN KEY ("portal_user_id") REFERENCES "stewardship_portal_user" ("id") DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX "stewardship_address_match_correlation_id_f963b34d" ON "stewardship_address_match" ("correlation_id");
CREATE INDEX "stewardship_address_match_portal_user_id_bb46f4f3" ON "stewardship_address_match" ("portal_user_id");

-- A security event comes from an activation or from a new match, never
-- both; a match's event names the matched account and, when the match took
-- the address over (replaced), the account it replaced and its last
-- sign-in. Adding nullable columns fires no row trigger, so the append-only
-- guard is not involved, and every existing row satisfies the new checks.
ALTER TABLE public.stewardship_policy_security_event
    ALTER COLUMN activation_id DROP NOT NULL,
    ADD COLUMN IF NOT EXISTS account_email varchar(254) NULL,
    ADD COLUMN IF NOT EXISTS replaced_account_email varchar(254) NULL,
    ADD COLUMN IF NOT EXISTS replaced_login_at timestamp with time zone NULL,
    ADD COLUMN IF NOT EXISTS address_match_id uuid NULL;
-- The unique constraint makes a match's event once-only: the activation
-- identity (activation_id, rule_record_id) treats null activations as
-- distinct. The foreign key is deferred, as every Django one is, because
-- the match guard writes the event before its match row exists.
ALTER TABLE public.stewardship_policy_security_event
    ADD CONSTRAINT stewardship_policy_security_event_address_match_id_key UNIQUE (address_match_id);
ALTER TABLE "stewardship_policy_security_event" ADD CONSTRAINT "stewardship_policy_s_address_match_id_4e8e7c5a_fk_stewardsh" FOREIGN KEY ("address_match_id") REFERENCES "stewardship_address_match" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_policy_security_event" ADD CONSTRAINT "policy_security_event_source" CHECK (num_nonnulls("activation_id", "address_match_id") = 1);
ALTER TABLE "stewardship_policy_security_event" ADD CONSTRAINT "policy_security_event_account" CHECK ((("account_email" IS NOT NULL AND "address_match_id" IS NOT NULL AND "kind" = 'address_matched' AND "account_email" = (LOWER(TRIM("account_email")))) OR (NOT ("kind" = 'address_matched') AND "account_email" IS NULL AND "address_match_id" IS NULL AND "replaced_account_email" IS NULL)));
ALTER TABLE "stewardship_policy_security_event" ADD CONSTRAINT "policy_security_event_replaced" CHECK ((("replaced_account_email" IS NULL AND "replaced_login_at" IS NULL) OR ("replaced_account_email" IS NOT NULL AND "replaced_account_email" = (LOWER(TRIM("replaced_account_email"))))));

-- Whether an address is in use by a Google account other than `excluding`:
-- an enabled account whose sign-in address it is, or whose standing match
-- names it (whatever its email is now), with a live Admin session (not
-- revoked, inside its absolute and 60-minute idle limits, not signed in in
-- the future), a live automation session (stewardship_automation_live_v1),
-- or a successful Admin sign-in (an admin_login audit event) less than 30
-- days ago. Session rows alone cannot serve: cleanup deletes them within
-- hours of their end; the audit events are kept. `excluding` is compared
-- with IS DISTINCT FROM because the replaced end passes NULL, for which <>
-- would be NULL on every row. The page, the command line and the match
-- guard all ask this one function, so they cannot disagree.
CREATE FUNCTION public.stewardship_address_in_use_v1(address text, excluding uuid)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER
SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT EXISTS (
        SELECT 1 FROM public.stewardship_portal_user u
        WHERE NOT u.disabled AND u.id IS DISTINCT FROM excluding
          AND (u.sign_in_email=address OR EXISTS (
                SELECT 1 FROM public.stewardship_address_match m
                WHERE m.portal_user_id=u.id AND m.ended_at IS NULL AND m.email=address))
          AND (EXISTS (
                SELECT 1 FROM public.stewardship_portal_session s
                WHERE s.principal_id=u.id AND s.revoked_at IS NULL
                  AND s.expires_at>statement_timestamp()
                  AND s.last_activity_at>statement_timestamp()-interval '60 minutes'
                  AND s.authenticated_at<=statement_timestamp())
            OR EXISTS (
                SELECT 1 FROM public.stewardship_automation_session a
                WHERE a.principal_id=u.id AND a.revoked_at IS NULL
                  AND public.stewardship_automation_live_v1(a.id))
            OR EXISTS (
                SELECT 1 FROM public.stewardship_audit_event e
                WHERE e.actor_id=u.id AND e.event_type='admin_login'
                  AND e.created_at>statement_timestamp()-interval '30 days')))
$$;
REVOKE ALL ON FUNCTION public.stewardship_address_in_use_v1(text, uuid) FROM PUBLIC;

-- Written in the quoted per-column form every generated mutable guard uses,
-- which test_all_concrete_mutable_records_have_enabled_guard checks against
-- the model's immutable and write-once fields.
CREATE FUNCTION public.stewardship_address_match_mutable_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF NEW."id" IS DISTINCT FROM OLD."id"
       OR NEW."created_at" IS DISTINCT FROM OLD."created_at"
       OR NEW."portal_user_id" IS DISTINCT FROM OLD."portal_user_id"
       OR NEW."email" IS DISTINCT FROM OLD."email"
       OR NEW."user_record_id" IS DISTINCT FROM OLD."user_record_id"
       OR NEW."sign_in_email" IS DISTINCT FROM OLD."sign_in_email"
       OR NEW."matched_at" IS DISTINCT FROM OLD."matched_at"
       OR (OLD."ended_at" IS NOT NULL AND NEW."ended_at" IS DISTINCT FROM OLD."ended_at")
       OR (OLD."end_reason" IS NOT NULL AND NEW."end_reason" IS DISTINCT FROM OLD."end_reason")
       OR (OLD."ended_by_id" IS NOT NULL AND NEW."ended_by_id" IS DISTINCT FROM OLD."ended_by_id")
       OR (OLD."ended_session_id" IS NOT NULL AND NEW."ended_session_id" IS DISTINCT FROM OLD."ended_session_id") THEN
        RAISE EXCEPTION 'Record identity and bindings are immutable' USING ERRCODE='23514';
    END IF;
    IF NEW.version IS DISTINCT FROM OLD.version + 1 THEN
        RAISE EXCEPTION 'Every update must advance the record version' USING ERRCODE='23514';
    END IF;
    NEW.updated_at:=statement_timestamp();
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.stewardship_address_match_mutable_v1() FROM PUBLIC;

-- The match guard (data spec "The match guard"). Every refusal is a check
-- violation (23514), or 42501 for a delete or the wrong login, actor or
-- session; an insert refused because the address is in use names
-- address_own_account_in_use in its detail.
CREATE FUNCTION public.stewardship_address_match_guard_v1() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE
    instant timestamptz := statement_timestamp();
    account public.stewardship_portal_user%ROWTYPE;
    user_roles jsonb;
    replaced_email text;
    replaced_account uuid;
    replaced_login timestamptz;
    automation_reason text;
BEGIN
    -- The schema owner (migrations, tests, a documented repair) passes
    -- unchecked and writes nothing: it can already drop or replace this
    -- guard. Inside a definer current_user is the owner, so the login is
    -- the session's.
    IF pg_has_role(session_user,
        (SELECT nspowner FROM pg_namespace WHERE nspname='public'),'USAGE') THEN
        IF TG_OP='DELETE' THEN RETURN OLD; END IF;
        RETURN NEW;
    END IF;
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'A verified-address match is kept for attribution' USING ERRCODE='42501';
    END IF;
    IF session_user<>'pk_stewardship_web' THEN
        RAISE EXCEPTION 'Only the web login records verified-address matches' USING ERRCODE='42501';
    END IF;
    -- A match and a user change are decided one at a time: configuration
    -- activation takes the same lock.
    PERFORM pg_advisory_xact_lock(736220,1);

    IF TG_OP='INSERT' THEN
        SELECT * INTO account FROM public.stewardship_portal_user u
        WHERE u.id=NEW.portal_user_id;
        -- A user of the active configuration is an address rule granting an
        -- Admin role, the projection the session admission guard reads.
        SELECT a.roles INTO user_roles
        FROM public.stewardship_system_configuration r
        JOIN public.stewardship_address_rule a ON a.configuration_id=r.active_configuration_id
        WHERE a.email=NEW.email AND a.record_id=NEW.user_record_id
          AND a.roles OPERATOR(pg_catalog.?|) ARRAY['administrator','staff','ministry_leader'];
        -- The second sign-in step just verified the account, whose sign-in
        -- address is the row's and is not the matched address, which is a
        -- current user's with the row's record ID; a new match has no end.
        -- It names no actor, as a new portal user names none (0030): the
        -- audit row and the event attribute it to the account, and the
        -- update guard keeps actor_id as it is, so a forged one would stick.
        IF account.id IS NULL OR account.disabled OR NEW.actor_id IS NOT NULL
           OR account.verified_at < transaction_timestamp()
           OR account.verified_at > instant
           OR account.sign_in_email IS DISTINCT FROM NEW.sign_in_email
           OR NEW.email IS NOT DISTINCT FROM NEW.sign_in_email
           OR user_roles IS NULL
           OR NEW.ended_at IS NOT NULL OR NEW.end_reason IS NOT NULL
           OR NEW.ended_by_id IS NOT NULL OR NEW.ended_session_id IS NOT NULL THEN
            RAISE EXCEPTION 'A verified-address match needs an account verified in this transaction and a current user'
                USING ERRCODE='23514';
        END IF;
        -- A first-Administrator address (one the root configuration, the
        -- version with no predecessor, made Administrator) only ever signs
        -- in with its own Google account.
        IF EXISTS (SELECT 1 FROM public.stewardship_address_rule a
            JOIN public.stewardship_configuration_version v ON v.id=a.configuration_id
            WHERE v.predecessor_id IS NULL AND a.email=NEW.email
              AND a.roles OPERATOR(pg_catalog.?) 'administrator') THEN
            RAISE EXCEPTION 'A first Administrator address signs in only with its own Google account'
                USING ERRCODE='23514', DETAIL='address_first_administrator';
        END IF;
        IF public.stewardship_address_in_use_v1(NEW.email, NEW.portal_user_id) THEN
            RAISE EXCEPTION 'The address is in use by another Google account'
                USING ERRCODE='23514', DETAIL='address_own_account_in_use';
        END IF;
        NEW.matched_at := instant;
        NEW.confirmed_at := instant;
        NEW.version := 1;
        NEW.created_at := instant;
        NEW.updated_at := instant;
        -- When this transaction ended another account's standing match of
        -- the address as replaced, the notice names that account and its
        -- last sign-in.
        SELECT m.sign_in_email, m.portal_user_id INTO replaced_email, replaced_account
        FROM public.stewardship_address_match m
        WHERE m.email=NEW.email AND m.end_reason='replaced'
          AND m.ended_at>=transaction_timestamp()
        ORDER BY m.ended_at DESC LIMIT 1;
        IF replaced_account IS NOT NULL THEN
            SELECT max(e.created_at) INTO replaced_login
            FROM public.stewardship_audit_event e
            WHERE e.actor_id=replaced_account AND e.event_type='admin_login';
        END IF;
        INSERT INTO public.stewardship_audit_event
            (id, actor_id, correlation_id, event_type, subject_id)
        VALUES (gen_random_uuid(), NEW.portal_user_id, NEW.correlation_id,
            'admin_address_matched', NEW.id);
        INSERT INTO public.stewardship_policy_security_event
            (id, created_at, actor_id, correlation_id, activation_id, address_match_id,
             rule_record_id, target, kind, before_roles, after_roles, recipients,
             account_email, replaced_account_email, replaced_login_at)
        SELECT gen_random_uuid(), instant, NEW.portal_user_id, NEW.correlation_id, NULL, NEW.id,
            NEW.user_record_id, NEW.email, 'address_matched', '[]'::jsonb, user_roles,
            COALESCE((SELECT jsonb_agg(a.email ORDER BY a.email)
                FROM public.stewardship_system_configuration r
                JOIN public.stewardship_address_rule a ON a.configuration_id=r.active_configuration_id
                WHERE a.roles OPERATOR(pg_catalog.?) 'administrator'), '[]'::jsonb),
            NEW.sign_in_email, replaced_email, replaced_login;
        RETURN NEW;
    END IF;

    IF OLD.ended_at IS NOT NULL THEN
        RAISE EXCEPTION 'An ended verified-address match does not change' USING ERRCODE='23514';
    END IF;
    IF (NEW.id, NEW.created_at, NEW.actor_id, NEW.portal_user_id, NEW.email,
        NEW.user_record_id, NEW.sign_in_email, NEW.matched_at)
       IS DISTINCT FROM (OLD.id, OLD.created_at, OLD.actor_id, OLD.portal_user_id, OLD.email,
        OLD.user_record_id, OLD.sign_in_email, OLD.matched_at) THEN
        RAISE EXCEPTION 'A verified-address match keeps its account and address' USING ERRCODE='23514';
    END IF;
    SELECT * INTO account FROM public.stewardship_portal_user u WHERE u.id=OLD.portal_user_id;

    IF NEW.ended_at IS NULL THEN
        -- A re-check: only confirmed_at moves, to now, after a second
        -- sign-in step verified the account in this transaction.
        IF NEW.end_reason IS NOT NULL OR NEW.ended_by_id IS NOT NULL
           OR NEW.ended_session_id IS NOT NULL
           OR account.verified_at < transaction_timestamp()
           OR account.verified_at > instant THEN
            RAISE EXCEPTION 'A verified-address match is confirmed again only by a sign-in in this transaction'
                USING ERRCODE='23514';
        END IF;
        NEW.confirmed_at := instant;
        RETURN NEW;
    END IF;

    -- An end, once, changing only the end columns (and the version), after
    -- the caller put the account's address back.
    IF NEW.confirmed_at IS DISTINCT FROM OLD.confirmed_at
       OR account.email IS NOT DISTINCT FROM OLD.email THEN
        RAISE EXCEPTION 'A verified-address match ends only after its account acts as its own address again'
            USING ERRCODE='23514';
    END IF;
    NEW.ended_at := instant;
    IF NEW.end_reason='ended_by_administrator' THEN
        -- An enabled Administrator now, not the match's own account, in a
        -- live Admin session: the conditions of the backup request guard's
        -- latest definition (0124).
        IF NEW.ended_by_id IS NULL OR NEW.ended_by_id=OLD.portal_user_id
           OR public.stewardship_export_authorized_v1(NEW.ended_by_id,true) IS NOT TRUE
           OR NOT EXISTS(SELECT 1 FROM public.stewardship_portal_session login
                WHERE login.id=NEW.ended_session_id AND login.principal_id=NEW.ended_by_id
                  AND login.revoked_at IS NULL AND login.expires_at>clock_timestamp()
                  AND login.last_activity_at>clock_timestamp()-interval '60 minutes'
                  AND login.authenticated_at<=clock_timestamp()
                  AND (NOT EXISTS (SELECT 1 FROM public.stewardship_automation_login command
                           WHERE command.portal_session_id=login.id)
                       OR public.stewardship_automation_fresh_v1(login.id,NEW.ended_by_id))) THEN
            RAISE EXCEPTION 'Ending a verified-address match requires a live Administrator session'
                USING ERRCODE='42501';
        END IF;
    ELSIF NEW.ended_by_id IS NOT NULL OR NEW.ended_session_id IS NOT NULL THEN
        RAISE EXCEPTION 'Only an Administrator end names an actor' USING ERRCODE='42501';
    ELSIF NEW.end_reason='user_gone' THEN
        -- No current user has the address with the matched record ID (the
        -- user was deleted, or deleted and added again as a new record).
        IF EXISTS (SELECT 1 FROM public.stewardship_system_configuration r
            JOIN public.stewardship_address_rule a ON a.configuration_id=r.active_configuration_id
            WHERE a.email=OLD.email AND a.record_id=OLD.user_record_id
              AND a.roles OPERATOR(pg_catalog.?|) ARRAY['administrator','staff','ministry_leader']) THEN
            RAISE EXCEPTION 'The matched user is still there' USING ERRCODE='23514';
        END IF;
    ELSIF NEW.end_reason='sign_in_address_matches' THEN
        IF NOT EXISTS (SELECT 1 FROM public.stewardship_system_configuration r
            JOIN public.stewardship_address_rule a ON a.configuration_id=r.active_configuration_id
            WHERE a.email=account.sign_in_email
              AND a.roles OPERATOR(pg_catalog.?|) ARRAY['administrator','staff','ministry_leader']) THEN
            RAISE EXCEPTION 'The account''s sign-in address is no user''s' USING ERRCODE='23514';
        END IF;
    ELSIF NEW.end_reason='replaced' THEN
        -- Another account takes the address over only when the match's
        -- check lapsed and neither it nor the address's own account signed
        -- in for 30 days or holds a live session.
        IF OLD.confirmed_at>instant-interval '30 days'
           OR public.stewardship_address_in_use_v1(OLD.email, NULL) THEN
            RAISE EXCEPTION 'The address is in use by another Google account'
                USING ERRCODE='23514', DETAIL='address_own_account_in_use';
        END IF;
    ELSIF NEW.end_reason='not_confirmed' THEN
        IF account.verified_at < transaction_timestamp() OR account.verified_at > instant THEN
            RAISE EXCEPTION 'A verified-address match is found unconfirmed only by a sign-in in this transaction'
                USING ERRCODE='23514';
        END IF;
    ELSE
        RAISE EXCEPTION 'A verified-address match ends with a known reason' USING ERRCODE='23514';
    END IF;

    -- The end's audit row; its reason is the match's own end_reason. Its
    -- actor is the Administrator, nobody for a takeover (another account's
    -- sign-in), or else the account whose own sign-in ended it.
    INSERT INTO public.stewardship_audit_event
        (id, actor_id, correlation_id, event_type, subject_id)
    VALUES (gen_random_uuid(),
        CASE NEW.end_reason WHEN 'ended_by_administrator' THEN NEW.ended_by_id
            WHEN 'replaced' THEN NULL ELSE OLD.portal_user_id END,
        NEW.correlation_id, 'admin_address_match_ended', OLD.id);
    -- An Administrator's end, a takeover and a gone user end the old
    -- account's live sessions in the same statement, so no row left behind
    -- could serve again if a later match pointed its email back. Each as
    -- sessions._revoke and automation_sessions.end_session record it.
    IF NEW.end_reason IN ('ended_by_administrator','replaced','user_gone') THEN
        WITH ended AS (
            UPDATE public.stewardship_portal_session s
            SET revoked_at=GREATEST(instant, s.last_activity_at, s.authenticated_at),
                version=s.version+1
            WHERE s.principal_id=OLD.portal_user_id AND s.revoked_at IS NULL
              AND s.expires_at>instant AND s.last_activity_at>instant-interval '60 minutes'
            RETURNING s.id)
        INSERT INTO public.stewardship_audit_event
            (id, actor_id, correlation_id, event_type, subject_id)
        SELECT gen_random_uuid(), OLD.portal_user_id, NEW.correlation_id, 'admin_revoked', ended.id
        FROM ended;
        automation_reason := CASE NEW.end_reason
            WHEN 'ended_by_administrator' THEN 'revoked_by_administrator'
            WHEN 'replaced' THEN 'role_lost' ELSE 'user_removed' END;
        WITH ended AS (
            UPDATE public.stewardship_automation_session a
            SET revoked_at=GREATEST(instant, a.created_at), end_reason=automation_reason,
                version=a.version+1, actor_id=NEW.ended_by_id, correlation_id=NEW.correlation_id
            WHERE a.principal_id=OLD.portal_user_id AND a.revoked_at IS NULL
              AND a.expires_at>instant
            RETURNING a.id
        ), audited AS (
            INSERT INTO public.stewardship_audit_event
                (id, actor_id, correlation_id, event_type, subject_id)
            SELECT gen_random_uuid(), NEW.ended_by_id, NEW.correlation_id,
                'automation_session_ended', ended.id
            FROM ended
            RETURNING id
        ), described AS (
            INSERT INTO public.stewardship_audit_context
                (id, actor_id, correlation_id, actor_kind, schema, context, event_id)
            SELECT gen_random_uuid(), NEW.ended_by_id, NEW.correlation_id,
                CASE WHEN NEW.ended_by_id IS NULL THEN 'system' ELSE 'portal_user' END,
                'action', '{"outcome": "succeeded"}'::jsonb, audited.id
            FROM audited
        )
        INSERT INTO public.stewardship_automation_notice
            (id, created_at, actor_id, correlation_id, kind, automation_session_id)
        SELECT gen_random_uuid(), instant, NEW.ended_by_id, NEW.correlation_id, 'ended', ended.id
        FROM ended;
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.stewardship_address_match_guard_v1() FROM PUBLIC;

-- Triggers fire in name order: the definer guard decides first, then the
-- generic guard checks the identity columns and the version.
CREATE TRIGGER stewardship_address_match_guard
    BEFORE INSERT OR UPDATE OR DELETE ON public.stewardship_address_match
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_address_match_guard_v1();
CREATE TRIGGER stewardship_address_match_mutable_guard_v1
    BEFORE UPDATE ON public.stewardship_address_match
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_address_match_mutable_v1();

-- stewardship_security_content_v2, from 0043_alert_local_time.sql: the
-- address_matched kind (title, instruction and rows; the Python twin is
-- jobs/security_content.py). Still a STABLE invoker with the fixed
-- search_path, so no ALTER follows.
CREATE OR REPLACE FUNCTION public.stewardship_security_content_v2(event uuid, deployment_mode text,
    configuration uuid)
RETURNS jsonb LANGUAGE plpgsql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE e stewardship_policy_security_event%ROWTYPE; title text; actor text;
    instruction text; labels text[]; vals text[]; body text; html text; i integer;
BEGIN
    IF deployment_mode IS NULL OR deployment_mode NOT IN ('testing','production') THEN
      RAISE EXCEPTION 'Security alert mode is invalid' USING ERRCODE='23514'; END IF;
    SELECT * INTO STRICT e FROM stewardship_policy_security_event WHERE id=event;
    title:=CASE e.kind
      WHEN 'administrator_granted' THEN 'Administrator added to an exact address'
      WHEN 'domain_created' THEN 'Hosted-domain rule created'
      WHEN 'domain_staff_granted' THEN 'Staff added to a hosted-domain rule'
      WHEN 'backup_key_replaced' THEN 'Backup encryption key replaced'
      WHEN 'address_matched' THEN 'Google account matched through another address'
    END;
    IF title IS NULL THEN RAISE EXCEPTION 'Security alert kind is invalid' USING ERRCODE='23514'; END IF;
    SELECT email INTO actor FROM stewardship_portal_user WHERE id=e.actor_id;
    IF e.kind='backup_key_replaced' THEN
      instruction:='An Administrator replaced the public key that backups are encrypted '
        'with. From the next backup on, only the matching private key can open them. '
        'If the parish key holder did not plan this, replace the key again with the '
        'parish key and ask the server operator to check the newest backup. If you '
        'can still sign in as an Administrator, acknowledge it on the Admin dashboard; '
        'it stays there until an Administrator other than the one who made the change '
        'does. If you no longer can, contact the parish or its other Administrators.';
      labels:=ARRAY['Key before','Key after','By','When','Deployment mode',
        'Event reference'];
      vals:=ARRAY[COALESCE(e.before_roles->>0,'not known'),e.after_roles->>0,
        COALESCE(actor,'Operator recovery'),
        stewardship_alert_time_v1(e.created_at,configuration),
        initcap(deployment_mode),e.id::text];
    ELSIF e.kind='address_matched' THEN
      -- A new verified-address match (#2343): the matched Google account,
      -- the user it signs in as and that user's role, and, after a
      -- takeover, the Google account it replaced. No By row: the account
      -- itself is the actor.
      instruction:='A Google account signed in to the Admin portal as a user, through '
        'another address of that account that Google confirmed belongs to it. If this '
        'was not expected, end the match on the Edit page of that user, or delete the '
        'user if the address itself is not safe. Acknowledge it on the Admin dashboard; '
        'it stays there until an Administrator other than the matched person does.';
      labels:=ARRAY['Account','User','Role'];
      vals:=ARRAY[e.account_email,e.target,stewardship_security_role_words_v1(e.after_roles)];
      IF e.replaced_account_email IS NOT NULL THEN
        labels:=labels||ARRAY['Replaced Google account'];
        vals:=vals||ARRAY[e.replaced_account_email||CASE WHEN e.replaced_login_at IS NULL
          THEN ', never signed in'
          ELSE ', last signed in '||stewardship_alert_time_v1(e.replaced_login_at,configuration)
          END];
      END IF;
      labels:=labels||ARRAY['When','Deployment mode','Event reference'];
      vals:=vals||ARRAY[stewardship_alert_time_v1(e.created_at,configuration),
        initcap(deployment_mode),e.id::text];
    ELSE
    instruction:='This change to who may sign in took effect on activation. Acknowledge it '
      'on the Admin dashboard; it stays there until an Administrator who existed '
      'before the change does.';
    labels:=ARRAY['Target','Roles before','Roles after','By','When','Deployment mode',
      'Event reference'];
    vals:=ARRAY[e.target,stewardship_security_role_words_v1(e.before_roles),
      stewardship_security_role_words_v1(e.after_roles),COALESCE(actor,'Operator recovery'),
      stewardship_alert_time_v1(e.created_at,configuration),
      initcap(deployment_mode),e.id::text];
    END IF;
    body:=title||E'\n\n'||instruction||E'\n\n';
    html:='<h2>'||title||'</h2><p>'||instruction||'</p><dl>';
    FOR i IN 1..array_length(labels,1) LOOP
      body:=body||CASE WHEN i>1 THEN E'\n' ELSE '' END||labels[i]||': '||vals[i];
      html:=html||'<dt>'||labels[i]||'</dt><dd>'||stewardship_security_escape_v1(vals[i])||'</dd>';
    END LOOP;
    RETURN jsonb_build_object('subject','['||upper(deployment_mode)||'] SECURITY: '||title,
      'html',html||'</dl>','text',body);
END $$;

DO $check$
DECLARE pinned text[] := ARRAY['search_path=pg_catalog, public, pg_temp'];
BEGIN
    -- The sign-in address: present, NOT NULL and, since no match exists
    -- yet, equal to email on every row.
    IF NOT EXISTS (SELECT 1 FROM pg_attribute
        WHERE attrelid='public.stewardship_portal_user'::regclass
          AND attname='sign_in_email' AND attnotnull AND NOT attisdropped)
       OR EXISTS (SELECT 1 FROM public.stewardship_portal_user
                  WHERE sign_in_email IS DISTINCT FROM email) THEN
        RAISE EXCEPTION 'Migration 0200: the portal user sign-in address is not installed';
    END IF;
    -- The match table: its partial unique indexes, Timeline index, checks
    -- and foreign key.
    IF to_regclass('public.stewardship_address_match') IS NULL
       OR (SELECT count(*) FROM pg_index x JOIN pg_class i ON i.oid=x.indexrelid
           WHERE x.indrelid='public.stewardship_address_match'::regclass
             AND i.relname IN ('address_match_one_per_account','address_match_one_per_address')
             AND x.indisunique AND x.indpred IS NOT NULL)<>2
       OR to_regclass('public.address_match_email_time') IS NULL
       OR (SELECT count(*) FROM pg_constraint
           WHERE conrelid='public.stewardship_address_match'::regclass AND contype='c'
             AND conname IN ('address_match_normalized','address_match_addresses_differ',
                             'address_match_end',
                             'stewardship_accounts_addressmatch_positive_version'))<>4
       OR (SELECT count(*) FROM pg_constraint
           WHERE conrelid='public.stewardship_address_match'::regclass AND contype='f')<>1 THEN
        RAISE EXCEPTION 'Migration 0200: the verified-address match table is not installed';
    END IF;
    -- Its two row triggers, and no TRUNCATE trigger (the owner flushes it).
    IF NOT EXISTS (SELECT 1 FROM pg_trigger t
        WHERE t.tgrelid='public.stewardship_address_match'::regclass
          AND t.tgname='stewardship_address_match_guard' AND NOT t.tgisinternal
          AND t.tgenabled='O'
          -- A row trigger, before insert, delete and update (1+2+4+8+16).
          AND t.tgtype=31
          AND t.tgfoid='public.stewardship_address_match_guard_v1()'::regprocedure)
       OR NOT EXISTS (SELECT 1 FROM pg_trigger t
        WHERE t.tgrelid='public.stewardship_address_match'::regclass
          AND t.tgname='stewardship_address_match_mutable_guard_v1' AND NOT t.tgisinternal
          AND t.tgenabled='O'
          -- A row trigger, before update (1+2+16).
          AND t.tgtype=19
          AND t.tgfoid='public.stewardship_address_match_mutable_v1()'::regprocedure)
       OR EXISTS (SELECT 1 FROM pg_trigger t
        WHERE t.tgrelid='public.stewardship_address_match'::regclass
          AND NOT t.tgisinternal AND t.tgtype & 32 <> 0) THEN
        RAISE EXCEPTION 'Migration 0200: the verified-address match guards are not installed';
    END IF;
    -- The security event: nullable activation, the match's columns, its
    -- once-only unique constraint, deferred foreign key and three checks.
    IF EXISTS (SELECT 1 FROM pg_attribute
        WHERE attrelid='public.stewardship_policy_security_event'::regclass
          AND attname='activation_id' AND attnotnull)
       OR (SELECT count(*) FROM pg_attribute
           WHERE attrelid='public.stewardship_policy_security_event'::regclass
             AND attname IN ('address_match_id','account_email','replaced_account_email',
                             'replaced_login_at')
             AND NOT attnotnull AND NOT attisdropped)<>4
       OR NOT EXISTS (SELECT 1 FROM pg_constraint
           WHERE conrelid='public.stewardship_policy_security_event'::regclass
             AND conname='stewardship_policy_security_event_address_match_id_key'
             AND contype='u')
       OR NOT EXISTS (SELECT 1 FROM pg_constraint
           WHERE conrelid='public.stewardship_policy_security_event'::regclass
             AND contype='f' AND condeferrable AND condeferred
             AND confrelid='public.stewardship_address_match'::regclass)
       OR (SELECT count(*) FROM pg_constraint
           WHERE conrelid='public.stewardship_policy_security_event'::regclass AND contype='c'
             AND conname IN ('policy_security_event_source','policy_security_event_account',
                             'policy_security_event_replaced'))<>3 THEN
        RAISE EXCEPTION 'Migration 0200: the address_matched security event is not installed';
    END IF;
    -- The two definers are definers with no PUBLIC EXECUTE; the replaced
    -- invokers and the match's mutable guard are invokers; all six pin
    -- exactly the search_path (CREATE OR REPLACE drops a SET clause the new
    -- text leaves out); each replaced body names what it now checks.
    IF (SELECT count(*) FROM pg_proc p
        WHERE p.proconfig=pinned
          AND ((p.oid=to_regprocedure('public.stewardship_address_match_guard_v1()')
                AND p.prosecdef
                AND strpos(p.prosrc,'address_own_account_in_use')>0
                AND strpos(p.prosrc,'OR NEW.actor_id IS NOT NULL')>0
                AND strpos(p.prosrc,'session_user<>''pk_stewardship_web''')>0
                AND strpos(p.prosrc,'stewardship_automation_fresh_v1(login.id,NEW.ended_by_id)')>0)
            OR (p.oid=to_regprocedure('public.stewardship_address_in_use_v1(text,uuid)')
                AND p.prosecdef
                AND strpos(p.prosrc,'u.id IS DISTINCT FROM excluding')>0)
            OR (p.oid=to_regprocedure('public.stewardship_address_match_mutable_v1()')
                AND NOT p.prosecdef)
            OR (p.oid=to_regprocedure('public.stewardship_portal_user_insert_v1()')
                AND NOT p.prosecdef
                AND strpos(p.prosrc,'NEW.sign_in_email IS DISTINCT FROM NEW.email')>0)
            OR (p.oid=to_regprocedure('public.stewardship_portal_user_mutable_v1()')
                AND NOT p.prosecdef
                AND strpos(p.prosrc,'NEW."sign_in_email"')>0
                AND strpos(p.prosrc,'public.stewardship_address_match m')>0)
            OR (p.oid=to_regprocedure('public.stewardship_security_content_v2(uuid,text,uuid)')
                AND NOT p.prosecdef AND p.provolatile='s'
                AND strpos(p.prosrc,'WHEN ''address_matched'' THEN')>0
                AND strpos(p.prosrc,'e.replaced_account_email')>0)))<>6
       OR has_function_privilege('public',
          'public.stewardship_address_match_guard_v1()','EXECUTE')
       OR has_function_privilege('public',
          'public.stewardship_address_in_use_v1(text,uuid)','EXECUTE') THEN
        RAISE EXCEPTION 'Migration 0200: a verified-address function is not installed as declared';
    END IF;
END
$check$;
