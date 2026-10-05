-- Frozen forward migration file 0004 (the repository-wide file sequence;
-- Django's stewardship_accounts.0004): durable Admin automation sessions
-- (ADM-11 PR 2, #463). This file is installed by
-- accounts/migrations/0004_automation_sessions.py and must never change once
-- released; tests/stewardship/test_schema_migration_files.py pins its digest
-- and checks that its copies of the replaced functions still equal the
-- fresh-install baseline's. A later change gets its own numbered file. A
-- fresh install runs 0001, 0002, 0003 and then this; the baseline files
-- already carry the replaced bodies and the widened constraint, so the
-- install ends in the same catalog as an upgraded database.
--
-- An automation session lets the host command line act as one Administrator,
-- who approved it in a browser that signed in with Google within five
-- minutes, for at most 30 days. Only the SHA-256 digest of its secret is
-- stored. Each command opens an ordinary Admin session (a command session)
-- linked here, so every session-bound check applies unchanged. Notices tell
-- every Administrator about approvals, refused uses and endings; four new
-- incident kinds carry the most important ones to email and Slack through the
-- existing operational routes. Sessions and notices are never deleted.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

CREATE TABLE "stewardship_automation_session" ("id" uuid NOT NULL PRIMARY KEY, "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "actor_id" uuid NULL, "correlation_id" uuid NOT NULL, "updated_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "version" bigint NOT NULL CHECK ("version" >= 0), "principal_id" uuid NOT NULL, "approving_session_id" uuid NOT NULL, "authenticated_at" timestamp with time zone NOT NULL, "expires_at" timestamp with time zone NOT NULL, "scope" varchar(9) NOT NULL, "label" varchar(64) NOT NULL, "secret_digest" varchar(64) NOT NULL UNIQUE, "host_digest" varchar(64) NOT NULL, "last_used_at" timestamp with time zone NULL, "revoked_at" timestamp with time zone NULL, "end_reason" varchar(32) NULL);
ALTER TABLE "stewardship_automation_session" ADD CONSTRAINT "stewardship_accounts_automationsession_positive_version" CHECK ("version" >= 1);
ALTER TABLE "stewardship_automation_session" ADD CONSTRAINT "automation_session_scope" CHECK (((scope)::text = ANY ((ARRAY['read_only'::character varying, 'full'::character varying])::text[])));
ALTER TABLE "stewardship_automation_session" ADD CONSTRAINT "automation_session_digests" CHECK ((((secret_digest)::text ~ '^[0-9a-f]{64}$'::text) AND ((host_digest)::text ~ '^[0-9a-f]{64}$'::text)));
ALTER TABLE "stewardship_automation_session" ADD CONSTRAINT "automation_session_label" CHECK (((label)::text ~ '^[^[:cntrl:]]{1,64}$'::text));
ALTER TABLE "stewardship_automation_session" ADD CONSTRAINT "automation_session_lifetime" CHECK ((expires_at > authenticated_at));
ALTER TABLE "stewardship_automation_session" ADD CONSTRAINT "automation_session_ending" CHECK ((((end_reason IS NULL) AND (revoked_at IS NULL)) OR (((end_reason)::text = ANY ((ARRAY['logout'::character varying, 'revoked_by_owner'::character varying, 'revoked_by_administrator'::character varying, 'role_lost'::character varying, 'user_removed'::character varying, 'recovery'::character varying, 'restore'::character varying, 'revoked_by_operator'::character varying, 'host_mismatch'::character varying, 'misused'::character varying, 'pairing_abandoned'::character varying])::text[])) AND (revoked_at IS NOT NULL))));
CREATE INDEX "stewardship_automation_session_correlation_id_6fbfce97" ON "stewardship_automation_session" ("correlation_id");
CREATE INDEX "stewardship_automation_session_secret_digest_b03503f2_like" ON "stewardship_automation_session" ("secret_digest" varchar_pattern_ops);
CREATE INDEX "automation_session_principal" ON "stewardship_automation_session" ("principal_id", "revoked_at");

-- The command logins: one row per command session. The Admin session is an
-- opaque UUID with no foreign key, so session cleanup may delete it first.
CREATE TABLE "stewardship_automation_login" ("portal_session_id" uuid NOT NULL PRIMARY KEY, "automation_session_id" uuid NOT NULL, "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL);
ALTER TABLE "stewardship_automation_login" ADD CONSTRAINT "stewardship_automati_automation_session_i_fe3ae1b5_fk_stewardsh" FOREIGN KEY ("automation_session_id") REFERENCES "stewardship_automation_session" ("id") DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX "stewardship_automation_login_automation_session_id_fe3ae1b5" ON "stewardship_automation_login" ("automation_session_id");

CREATE TABLE "stewardship_automation_notice" ("id" uuid NOT NULL PRIMARY KEY, "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "actor_id" uuid NULL, "correlation_id" uuid NOT NULL, "kind" varchar(16) NOT NULL, "automation_session_id" uuid NULL, "command_type" varchar(64) NULL, "campaign_id" uuid NULL);
ALTER TABLE "stewardship_automation_notice" ADD CONSTRAINT "automation_notice_kind" CHECK (((kind)::text = ANY ((ARRAY['approved'::character varying, 'fresh_gated'::character varying, 'irreversible'::character varying, 'policy_change'::character varying, 'refused'::character varying, 'ended'::character varying])::text[])));
ALTER TABLE "stewardship_automation_notice" ADD CONSTRAINT "automation_notice_command_type" CHECK (((command_type IS NULL) OR ((command_type)::text ~ '^[a-z][a-z0-9_]{0,63}$'::text)));
ALTER TABLE "stewardship_automation_notice" ADD CONSTRAINT "stewardship_automati_automation_session_i_eb2c3b8e_fk_stewardsh" FOREIGN KEY ("automation_session_id") REFERENCES "stewardship_automation_session" ("id") DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX "stewardship_automation_notice_correlation_id_58c1e15a" ON "stewardship_automation_notice" ("correlation_id");
CREATE INDEX "stewardship_automation_notice_automation_session_id_eb2c3b8e" ON "stewardship_automation_notice" ("automation_session_id");
CREATE INDEX "automation_notice_created" ON "stewardship_automation_notice" ("created_at");

CREATE TABLE "stewardship_automation_notice_ack" ("id" uuid NOT NULL PRIMARY KEY, "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "actor_id" uuid NULL, "correlation_id" uuid NOT NULL, "notice_id" uuid NOT NULL, "administrator_id" uuid NOT NULL);
ALTER TABLE "stewardship_automation_notice_ack" ADD CONSTRAINT "automation_notice_ack_identity" UNIQUE ("notice_id", "administrator_id");
ALTER TABLE "stewardship_automation_notice_ack" ADD CONSTRAINT "stewardship_automati_notice_id_a0ed4463_fk_stewardsh" FOREIGN KEY ("notice_id") REFERENCES "stewardship_automation_notice" ("id") DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX "stewardship_automation_notice_ack_correlation_id_032d17f1" ON "stewardship_automation_notice_ack" ("correlation_id");
CREATE INDEX "stewardship_automation_notice_ack_notice_id_a0ed4463" ON "stewardship_automation_notice_ack" ("notice_id");

-- Whether a session is live: unrevoked, unexpired, its principal an enabled
-- portal user whose current rule grants Administrator (the projection of
-- stewardship_export_authorized_v1(principal, true)), and no offline
-- Admin-access recovery recorded after its approval. Python reads liveness
-- through this one definition. Invoker rights: each caller needs its own
-- SELECT grants on what it reads.
CREATE FUNCTION public.stewardship_automation_live_v1(session_uuid uuid) RETURNS boolean
LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT coalesce((SELECT s.revoked_at IS NULL
        AND s.expires_at>statement_timestamp()
        AND public.stewardship_export_authorized_v1(s.principal_id,true)
        AND NOT EXISTS (SELECT 1 FROM public.stewardship_admin_revocation r
                        WHERE r.created_at>s.created_at)
        FROM public.stewardship_automation_session s WHERE s.id=session_uuid),false)
$$;

-- True only when the command session `login` (a stewardship_portal_session
-- id) is linked to a live, full-scope automation session of `actor` whose
-- sign-in instant it carries. The fresh-gate guards of the second ADM-11
-- migration (PR 5) accept it in place of a sign-in within five minutes; a
-- browser session has no link row, so for it the predicate is always false.
CREATE FUNCTION public.stewardship_automation_fresh_v1(login uuid, actor uuid) RETURNS boolean
LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT EXISTS (SELECT 1 FROM public.stewardship_automation_login l
        JOIN public.stewardship_automation_session a ON a.id=l.automation_session_id
        JOIN public.stewardship_portal_session p ON p.id=l.portal_session_id
        WHERE l.portal_session_id=login AND a.principal_id=actor AND a.scope='full'
          AND p.authenticated_at=a.authenticated_at
          AND public.stewardship_automation_live_v1(a.id))
$$;

-- Deletes the Django sessions of ended Admin sessions, for the maintenance
-- task's session cleanup, without letting the general worker read any session
-- key: a key is a credential, and the worker holds no SELECT on it. Only the
-- named rows that are already ended (revoked, expired or idle past the
-- 60-minute limit) lose their Django session; the caller then deletes those
-- rows in the same transaction, so the deferred foreign key holds at commit.
-- SECURITY DEFINER for the key lookup; only the worker and the schema owner
-- may call it (EXECUTE comes from database-grants, runtime_functions).
CREATE FUNCTION public.stewardship_admin_session_purge_v1(sessions uuid[]) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE removed integer;
BEGIN
    IF session_user<>'pk_stewardship_worker' AND NOT pg_has_role(session_user,
        (SELECT nspowner FROM pg_namespace WHERE nspname='public'),'USAGE') THEN
        RAISE EXCEPTION 'Only the maintenance task purges Admin sessions' USING ERRCODE='42501';
    END IF;
    DELETE FROM public.django_session d USING public.stewardship_portal_session p
     WHERE p.id=ANY(sessions) AND d.session_key=p.session_id
       AND (p.revoked_at IS NOT NULL OR p.expires_at<=statement_timestamp()
            OR p.last_activity_at<=statement_timestamp()-interval '60 minutes');
    GET DIAGNOSTICS removed = ROW_COUNT;
    RETURN removed;
END $$;
REVOKE ALL ON FUNCTION public.stewardship_admin_session_purge_v1(uuid[]) FROM PUBLIC;

-- Written in the quoted per-column form every generated mutable guard uses,
-- which test_all_concrete_mutable_records_have_enabled_guard checks against
-- the model's immutable and write-once fields.
CREATE FUNCTION public.stewardship_automation_session_mutable_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF NEW."id" IS DISTINCT FROM OLD."id"
       OR NEW."created_at" IS DISTINCT FROM OLD."created_at"
       OR NEW."principal_id" IS DISTINCT FROM OLD."principal_id"
       OR NEW."approving_session_id" IS DISTINCT FROM OLD."approving_session_id"
       OR NEW."authenticated_at" IS DISTINCT FROM OLD."authenticated_at"
       OR NEW."expires_at" IS DISTINCT FROM OLD."expires_at"
       OR NEW."scope" IS DISTINCT FROM OLD."scope"
       OR NEW."label" IS DISTINCT FROM OLD."label"
       OR NEW."secret_digest" IS DISTINCT FROM OLD."secret_digest"
       OR NEW."host_digest" IS DISTINCT FROM OLD."host_digest"
       OR (OLD."revoked_at" IS NOT NULL AND NEW."revoked_at" IS DISTINCT FROM OLD."revoked_at")
       OR (OLD."end_reason" IS NOT NULL AND NEW."end_reason" IS DISTINCT FROM OLD."end_reason") THEN
        RAISE EXCEPTION 'Record identity and bindings are immutable' USING ERRCODE='23514';
    END IF;
    IF NEW.version IS DISTINCT FROM OLD.version + 1 THEN
        RAISE EXCEPTION 'Every update must advance the record version' USING ERRCODE='23514';
    END IF;
    NEW.updated_at:=statement_timestamp();
    RETURN NEW;
END $$;

-- The pairing rules (see the specification's "Pairing" and "Schema impact").
-- Insert: the web login only; the approving browser session is a live
-- session of the principal, active within the idle limit, that signed in
-- within five minutes with the recorded instant and is not a command session;
-- the principal is an Administrator; the deadline is in the future and at
-- most 30 days away. Update: only last_used_at forward to at most now (web),
-- and revoked_at with end_reason once and together: any reason from the web,
-- role_lost, user_removed or recovery from the worker (the maintenance task),
-- and from it only for a session that has in fact lapsed (no longer live),
-- restore or revoked_by_operator from the admin-recovery login. An ended
-- session never changes again, and no row is ever deleted. The schema owner
-- is exempt from the login checks only, as in the engagement guard, so
-- migrations and the disposable test schema can write directly.
CREATE FUNCTION public.stewardship_automation_session_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE owner boolean := pg_has_role(current_user,
    (SELECT nspowner FROM pg_namespace WHERE nspname='public'),'USAGE');
BEGIN
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'Automation sessions are kept for attribution' USING ERRCODE='23514';
    END IF;
    IF TG_OP='INSERT' THEN
        IF NOT owner AND current_user<>'pk_stewardship_web' THEN
            RAISE EXCEPTION 'Only the web login approves automation sessions' USING ERRCODE='42501';
        END IF;
        IF NEW.version<>1 OR NEW.revoked_at IS NOT NULL OR NEW.end_reason IS NOT NULL
           OR NEW.last_used_at IS NOT NULL OR NEW.created_at>statement_timestamp()
           OR NEW.expires_at<=statement_timestamp()
           OR NEW.expires_at>statement_timestamp()+interval '30 days'
           OR NEW.authenticated_at>statement_timestamp()
           OR NEW.authenticated_at<statement_timestamp()-interval '5 minutes' THEN
            RAISE EXCEPTION 'Automation session approval is outside its bounds' USING ERRCODE='23514';
        END IF;
        IF NOT public.stewardship_export_authorized_v1(NEW.principal_id,true)
           OR NOT EXISTS (SELECT 1 FROM public.stewardship_portal_session s
               WHERE s.id=NEW.approving_session_id AND s.principal_id=NEW.principal_id
                 AND s.revoked_at IS NULL AND s.expires_at>statement_timestamp()
                 AND s.last_activity_at>statement_timestamp()-interval '60 minutes'
                 AND s.authenticated_at=NEW.authenticated_at)
           OR EXISTS (SELECT 1 FROM public.stewardship_automation_login l
               WHERE l.portal_session_id=NEW.approving_session_id) THEN
            RAISE EXCEPTION 'Automation approval requires a freshly signed-in Administrator browser session' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    IF OLD.revoked_at IS NOT NULL THEN
        RAISE EXCEPTION 'An ended automation session cannot change' USING ERRCODE='23514';
    END IF;
    IF NEW.last_used_at IS DISTINCT FROM OLD.last_used_at
       AND ((NOT owner AND current_user<>'pk_stewardship_web')
            OR NEW.last_used_at IS NULL OR NEW.last_used_at>statement_timestamp()
            OR NEW.last_used_at<coalesce(OLD.last_used_at,OLD.created_at)) THEN
        RAISE EXCEPTION 'Automation session use is recorded forward by the web login only' USING ERRCODE='23514';
    END IF;
    IF NEW.revoked_at IS DISTINCT FROM OLD.revoked_at
       OR NEW.end_reason IS DISTINCT FROM OLD.end_reason THEN
        IF NEW.revoked_at IS NULL OR NEW.end_reason IS NULL
           OR NEW.revoked_at>statement_timestamp() OR NEW.revoked_at<OLD.created_at THEN
            RAISE EXCEPTION 'An automation session ends once, with its reason' USING ERRCODE='23514';
        END IF;
        IF NOT owner AND NOT (current_user='pk_stewardship_web'
            OR (current_user='pk_stewardship_worker'
                AND NEW.end_reason IN ('role_lost','user_removed','recovery')
                AND NOT public.stewardship_automation_live_v1(NEW.id))
            OR (current_user='pk_stewardship_admin_recovery'
                AND NEW.end_reason IN ('restore','revoked_by_operator'))) THEN
            RAISE EXCEPTION 'This login cannot end an automation session for this reason' USING ERRCODE='42501';
        END IF;
    END IF;
    RETURN NEW;
END $$;

-- A command login is inserted by the web login in the transaction that
-- created its Admin session: after the automation session was created, not
-- the approving browser session, for the same principal, with the automation
-- session's sign-in instant and a deadline no later than its own, while the
-- automation session is live. It never changes, and is deleted only once its
-- Admin session is gone (the maintenance task's cleanup).
CREATE FUNCTION public.stewardship_automation_login_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE owner boolean := pg_has_role(current_user,
    (SELECT nspowner FROM pg_namespace WHERE nspname='public'),'USAGE');
    automation public.stewardship_automation_session%ROWTYPE;
    login public.stewardship_portal_session%ROWTYPE;
BEGIN
    IF TG_OP='UPDATE' THEN
        RAISE EXCEPTION 'Automation command logins cannot change' USING ERRCODE='23514';
    END IF;
    IF TG_OP='DELETE' THEN
        IF EXISTS (SELECT 1 FROM public.stewardship_portal_session
                   WHERE id=OLD.portal_session_id) THEN
            RAISE EXCEPTION 'An automation command login is kept while its Admin session exists' USING ERRCODE='23514';
        END IF;
        RETURN OLD;
    END IF;
    IF NOT owner AND current_user<>'pk_stewardship_web' THEN
        RAISE EXCEPTION 'Only the web login opens automation command sessions' USING ERRCODE='42501';
    END IF;
    SELECT * INTO automation FROM public.stewardship_automation_session
        WHERE id=NEW.automation_session_id;
    SELECT * INTO login FROM public.stewardship_portal_session
        WHERE id=NEW.portal_session_id;
    IF automation.id IS NULL OR login.id IS NULL
       OR NOT public.stewardship_automation_live_v1(automation.id)
       OR login.created_at<transaction_timestamp()
       OR login.created_at<=automation.created_at
       OR login.id=automation.approving_session_id
       OR login.principal_id<>automation.principal_id
       OR login.authenticated_at<>automation.authenticated_at
       OR login.expires_at>automation.expires_at
       OR login.revoked_at IS NOT NULL
       OR NEW.created_at>statement_timestamp() THEN
        RAISE EXCEPTION 'An automation command login requires a new Admin session of its live automation session' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;

-- The general worker gains UPDATE (revoked_at, version) and DELETE on Admin
-- session rows for the maintenance task's cleanup. This guard keeps both to
-- rows that have already ended (revoked, expired, or idle past the 60-minute
-- limit of accounts/session_policy.py ADMIN_IDLE), so the worker can never
-- end or delete a live session, and a revocation changes nothing else. Every
-- other login is unaffected; their own guards still apply.
CREATE FUNCTION public.stewardship_portal_session_cleanup_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF current_user<>'pk_stewardship_worker' THEN
        IF TG_OP='DELETE' THEN RETURN OLD; END IF;
        RETURN NEW;
    END IF;
    IF OLD.revoked_at IS NULL AND OLD.expires_at>statement_timestamp()
       AND OLD.last_activity_at>statement_timestamp()-interval '60 minutes' THEN
        RAISE EXCEPTION 'The maintenance task may only end or delete an ended Admin session' USING ERRCODE='42501';
    END IF;
    IF TG_OP='DELETE' THEN
        RETURN OLD;
    END IF;
    IF NEW.revoked_at IS NULL
       OR NEW.last_activity_at IS DISTINCT FROM OLD.last_activity_at
       OR NEW.authenticated_at IS DISTINCT FROM OLD.authenticated_at THEN
        RAISE EXCEPTION 'The maintenance task only records an ended Admin session' USING ERRCODE='42501';
    END IF;
    RETURN NEW;
END $$;

-- Notices and acknowledgements are append-only.
CREATE FUNCTION public.stewardship_automation_notice_immutable_v1() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Historical records are append-only' USING ERRCODE='23514';
END $$;
CREATE FUNCTION public.stewardship_automation_notice_ack_immutable_v1() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Historical records are append-only' USING ERRCODE='23514';
END $$;

-- The web (approvals, command refusals and endings), the worker (endings
-- found by the maintenance task) and the admin-recovery login (the restore's
-- revocation) record notices; nothing else does.
CREATE FUNCTION public.stewardship_automation_notice_insert_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF current_user NOT IN ('pk_stewardship_web','pk_stewardship_worker',
                            'pk_stewardship_admin_recovery')
       AND NOT pg_has_role(current_user,
           (SELECT nspowner FROM pg_namespace WHERE nspname='public'),'USAGE') THEN
        RAISE EXCEPTION 'This login cannot record automation notices' USING ERRCODE='42501';
    END IF;
    IF NEW.created_at>statement_timestamp() THEN
        RAISE EXCEPTION 'An automation notice cannot be dated in the future' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;

-- An acknowledgement is one current Administrator's own, through the web,
-- while that Administrator has a live Admin session.
CREATE FUNCTION public.stewardship_automation_notice_ack_insert_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF current_user<>'pk_stewardship_web'
       AND NOT pg_has_role(current_user,
           (SELECT nspowner FROM pg_namespace WHERE nspname='public'),'USAGE') THEN
        RAISE EXCEPTION 'Only the web login acknowledges automation notices' USING ERRCODE='42501';
    END IF;
    IF NEW.actor_id IS DISTINCT FROM NEW.administrator_id
       OR NOT public.stewardship_export_authorized_v1(NEW.administrator_id,true)
       OR NOT EXISTS (SELECT 1 FROM public.stewardship_portal_session s
           WHERE s.principal_id=NEW.administrator_id AND s.revoked_at IS NULL
             AND s.expires_at>statement_timestamp()
             AND s.last_activity_at>statement_timestamp()-interval '60 minutes') THEN
        RAISE EXCEPTION 'A current Administrator acknowledges an automation notice for themselves' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER stewardship_automation_session_mutable_guard_v1 BEFORE UPDATE ON public.stewardship_automation_session
FOR EACH ROW EXECUTE FUNCTION public.stewardship_automation_session_mutable_v1();
CREATE TRIGGER stewardship_automation_session_guard BEFORE INSERT OR UPDATE OR DELETE ON public.stewardship_automation_session
FOR EACH ROW EXECUTE FUNCTION public.stewardship_automation_session_guard_v1();
CREATE TRIGGER stewardship_automation_login_guard BEFORE INSERT OR UPDATE OR DELETE ON public.stewardship_automation_login
FOR EACH ROW EXECUTE FUNCTION public.stewardship_automation_login_guard_v1();
CREATE TRIGGER stewardship_portal_session_cleanup_guard BEFORE UPDATE OR DELETE ON public.stewardship_portal_session
FOR EACH ROW EXECUTE FUNCTION public.stewardship_portal_session_cleanup_guard_v1();
CREATE TRIGGER stewardship_automation_notice_immutable_guard_v1 BEFORE UPDATE OR DELETE ON public.stewardship_automation_notice
FOR EACH ROW EXECUTE FUNCTION public.stewardship_automation_notice_immutable_v1();
CREATE TRIGGER stewardship_automation_notice_insert_guard BEFORE INSERT ON public.stewardship_automation_notice
FOR EACH ROW EXECUTE FUNCTION public.stewardship_automation_notice_insert_v1();
CREATE TRIGGER stewardship_automation_notice_ack_immutable_guard_v1 BEFORE UPDATE OR DELETE ON public.stewardship_automation_notice_ack
FOR EACH ROW EXECUTE FUNCTION public.stewardship_automation_notice_ack_immutable_v1();
CREATE TRIGGER stewardship_automation_notice_ack_insert_guard BEFORE INSERT ON public.stewardship_automation_notice_ack
FOR EACH ROW EXECUTE FUNCTION public.stewardship_automation_notice_ack_insert_v1();
REVOKE ALL ON FUNCTION public.stewardship_automation_session_mutable_v1() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.stewardship_automation_session_guard_v1() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.stewardship_automation_login_guard_v1() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.stewardship_portal_session_cleanup_guard_v1() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.stewardship_automation_notice_immutable_v1() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.stewardship_automation_notice_insert_v1() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.stewardship_automation_notice_ack_immutable_v1() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.stewardship_automation_notice_ack_insert_v1() FROM PUBLIC;

-- The four automation incident kinds join the closed list (copied from
-- operational_incidents.sql as it stands at this release).
ALTER TABLE public.stewardship_ops_incident DROP CONSTRAINT ops_incident_kind;
ALTER TABLE "stewardship_ops_incident" ADD CONSTRAINT "ops_incident_kind" CHECK ("kind"::text = ANY(ARRAY[('database_unavailable'::varchar)::text, ('storage_integrity'::varchar)::text, ('task_failed'::varchar)::text, ('system_failure'::varchar)::text, ('source_refresh_failed'::varchar)::text, ('source_stale'::varchar)::text, ('source_tenant_mismatch'::varchar)::text, ('source_destructive_change'::varchar)::text, ('mail_provider_unavailable'::varchar)::text, ('scheduler_lag'::varchar)::text, ('worker_unavailable'::varchar)::text, ('admin_abuse'::varchar)::text, ('family_abuse'::varchar)::text, ('limiter_unavailable'::varchar)::text, ('limiter_state_lost'::varchar)::text, ('publication_ambiguous'::varchar)::text, ('production_cleanup_failed'::varchar)::text, ('backup_rpo_breach'::varchar)::text, ('backup_offsite_failed'::varchar)::text, ('backup_key_changed'::varchar)::text, ('source_retention_failing'::varchar)::text, ('purge_inconsistency'::varchar)::text, ('purge_cleanup_failed'::varchar)::text, ('automation_approved'::varchar)::text, ('automation_irreversible'::varchar)::text, ('automation_policy_change'::varchar)::text, ('automation_refused'::varchar)::text]));

-- The web login may observe the automation kinds (operational_incidents.sql).
CREATE OR REPLACE FUNCTION public.stewardship_ops_incident_state_v1()
RETURNS trigger LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE instant timestamptz := statement_timestamp();
BEGIN
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'Operational episode history cannot be deleted' USING ERRCODE='23514';
    END IF;
    -- Web observes authentication signals and, for the Admin automation
    -- interface, the four automation kinds (approval, refused use, policy
    -- changes and irreversible actions taken through a session).
    IF session_user='pk_stewardship_web' AND NEW.kind NOT IN
      ('limiter_unavailable','limiter_state_lost','admin_abuse','family_abuse',
       'automation_approved','automation_irreversible','automation_policy_change',
       'automation_refused') THEN
        RAISE EXCEPTION 'Web operational signals are limited to authentication and automation' USING ERRCODE='23514';
    END IF;
    IF TG_OP='INSERT' THEN
        IF NEW.version<>1 OR NEW.action<>'observe' OR NEW.occurrences<>1
           OR NEW.level<>'WARNING' OR NEW.last_notice_at IS NOT NULL
           OR NEW.resolved_at IS NOT NULL OR NEW.first_seen>instant
           OR NOT isfinite(NEW.first_seen) OR NEW.first_seen<'0001-01-01T00:00:00Z'::timestamptz
           OR NEW.last_seen IS DISTINCT FROM NEW.first_seen THEN
            RAISE EXCEPTION 'Operational episode must start with one observation' USING ERRCODE='23514';
        END IF;
        NEW.level := NEW.signal_level;
        IF NEW.level='CRITICAL' THEN NEW.last_notice_at := NEW.first_seen; END IF;
        RETURN NEW;
    END IF;
    -- The writer supplies a signal, not a forged count, time or notification.
    IF OLD.resolved_at IS NOT NULL OR instant<OLD.last_seen
       OR ROW(NEW.level,NEW.last_seen,NEW.occurrences,NEW.last_notice_at,NEW.resolved_at)
          IS DISTINCT FROM ROW(OLD.level,OLD.last_seen,OLD.occurrences,OLD.last_notice_at,OLD.resolved_at) THEN
        RAISE EXCEPTION 'Operational episode derived state is owner controlled' USING ERRCODE='23514';
    END IF;
    IF NEW.action='resolve' THEN
        IF NEW.signal_level IS DISTINCT FROM OLD.signal_level THEN
            RAISE EXCEPTION 'Recovery cannot replace the observation' USING ERRCODE='23514';
        END IF;
        NEW.resolved_at := instant;
        IF OLD.last_notice_at IS NOT NULL THEN NEW.last_notice_at := instant; END IF;
    ELSIF NEW.action='observe' THEN
        NEW.last_seen := instant;
        NEW.occurrences := CASE WHEN OLD.occurrences=9223372036854775807
            THEN OLD.occurrences ELSE OLD.occurrences+1 END;
        IF OLD.level='CRITICAL' OR NEW.signal_level='CRITICAL'
           OR instant-OLD.first_seen >= make_interval(secs=>OLD.escalation_seconds) THEN
            NEW.level := 'CRITICAL';
            IF OLD.level='WARNING'
               OR instant-OLD.last_notice_at >= make_interval(secs=>OLD.suppression_seconds) THEN
                NEW.last_notice_at := instant;
            END IF;
        END IF;
    ELSE
        RAISE EXCEPTION 'Unknown operational episode action' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;

-- Their fixed titles, instruction and resolved text (operational_render.sql),
-- word for word as jobs/operational_content.py renders them.
CREATE OR REPLACE FUNCTION public.stewardship_ops_content_v1(notice uuid, deployment_mode text)
RETURNS jsonb LANGUAGE plpgsql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE n stewardship_ops_notice%ROWTYPE; kind text; title text; status text;
    instruction text; labels text[]; vals text[]; body text; html text; i integer;
BEGIN
    IF deployment_mode IS NULL OR deployment_mode NOT IN ('testing','production') THEN
      RAISE EXCEPTION 'Operational mode is invalid' USING ERRCODE='23514'; END IF;
    SELECT * INTO STRICT n FROM stewardship_ops_notice WHERE id=notice;
    SELECT incident.kind INTO STRICT kind FROM stewardship_ops_incident incident WHERE id=n.incident_id;
    title:=CASE kind
      WHEN 'database_unavailable' THEN 'Database unavailable'
      WHEN 'storage_integrity' THEN 'Storage integrity requires attention'
      WHEN 'task_failed' THEN 'Background task failed'
      WHEN 'system_failure' THEN 'System operation requires attention'
      WHEN 'source_refresh_failed' THEN 'Parish data refresh failed'
      WHEN 'source_stale' THEN 'Parish data refresh is overdue'
      WHEN 'source_tenant_mismatch' THEN 'Parish data organization mismatch'
      WHEN 'source_destructive_change' THEN 'Unexpected parish data loss'
      WHEN 'mail_provider_unavailable' THEN 'Email provider unavailable'
      WHEN 'scheduler_lag' THEN 'Scheduled work is overdue'
      WHEN 'worker_unavailable' THEN 'Background worker unavailable'
      WHEN 'admin_abuse' THEN 'Sustained administration login abuse'
      WHEN 'family_abuse' THEN 'Sustained Family login abuse'
      WHEN 'limiter_unavailable' THEN 'Login rate limiter unavailable'
      WHEN 'limiter_state_lost' THEN 'Login rate limiter state was lost'
      WHEN 'publication_ambiguous' THEN 'Parish data publication is uncertain'
      WHEN 'production_cleanup_failed' THEN 'Campaign preparation cleanup failed'
      WHEN 'backup_rpo_breach' THEN 'Required backup is overdue'
      WHEN 'backup_offsite_failed' THEN 'Off-site backup copy failed'
      WHEN 'backup_key_changed' THEN 'Backup encryption key changed'
      WHEN 'source_retention_failing' THEN 'Parish data cleanup keeps failing'
      WHEN 'purge_inconsistency' THEN 'Campaign purge is inconsistent'
      WHEN 'purge_cleanup_failed' THEN 'Campaign purge cleanup failed'
      WHEN 'automation_approved' THEN 'An automation session was approved'
      WHEN 'automation_irreversible' THEN 'An automation session took an irreversible action'
      WHEN 'automation_policy_change' THEN 'An automation session changed user access, integration keys or notification settings'
      WHEN 'automation_refused' THEN 'An automation session was refused'
    END;
    IF title IS NULL THEN RAISE EXCEPTION 'Operational kind is invalid' USING ERRCODE='23514'; END IF;
    status:=CASE n.phase WHEN 'resolved' THEN 'RESOLVED' ELSE n.level END;
    instruction:=CASE WHEN n.phase='resolved' AND kind='backup_key_changed'
      THEN 'The backup encryption key change is no longer recent. If you have not already, ask the server operator to confirm that each kept copy of the private key opens a new backup, as the backup runbook describes.'
      WHEN n.phase='resolved' AND kind IN ('automation_approved','automation_irreversible',
        'automation_policy_change','automation_refused')
      THEN 'No further automation events of this kind in the last hour. Review the automation notices on the Admin dashboard if you have not already.'
      WHEN n.phase='resolved'
      THEN 'This condition has recovered. Review the operational log if follow-up is needed.'
      WHEN kind='backup_key_changed' THEN 'A backup in the last two days was sealed to a different encryption key than the backup before it. Unless the server operator installed a new key on purpose, new backups may not open with the kept private key. Ask the operator to open the newest backup with each kept copy of the private key, as the backup runbook describes.'
      WHEN kind IN ('automation_approved','automation_irreversible',
        'automation_policy_change','automation_refused')
      THEN 'Review the automation notices on the Admin dashboard. They name the automation session and what it did.'
      WHEN kind='source_retention_failing' THEN 'Removing old ParishSoft copies was skipped by the last three refreshes, so the database keeps growing. Refreshes still work. Ask the server operator to check the worker log for the cause.'
      ELSE 'Administrator attention is required. Review the operational log for details.' END;
    labels:=ARRAY['Status','Notification','Deployment mode','First observed',
      'Latest observation','Occurrences','Incident reference'];
    vals:=ARRAY[status,n.phase,initcap(deployment_mode),
      to_char(n.first_seen AT TIME ZONE 'UTC','MM/DD/YYYY HH24:MI:SS "UTC"'),
      to_char(n.observed_at AT TIME ZONE 'UTC','MM/DD/YYYY HH24:MI:SS "UTC"'),
      to_char(n.occurrences,'FM9,999,999,999,999,999,999'),n.incident_id::text];
    body:=title||E'\n\n'||instruction||E'\n\n';
    html:='<h2>'||title||'</h2><p>'||instruction||'</p><dl>';
    FOR i IN 1..array_length(labels,1) LOOP
      body:=body||CASE WHEN i>1 THEN E'\n' ELSE '' END||labels[i]||': '||vals[i];
      html:=html||'<dt>'||labels[i]||'</dt><dd>'||vals[i]||'</dd>';
    END LOOP;
    RETURN jsonb_build_object('subject','['||upper(deployment_mode)||'] '||status||': '||title,
      'html',html||'</dl>','text',body);
END $$;

-- The maintenance task runs in the general worker (functions.sql).
CREATE OR REPLACE FUNCTION public.stewardship_task_type_login_v1(task_type text) RETURNS text
    LANGUAGE sql IMMUTABLE STRICT
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$ SELECT CASE
        -- The login whose compiled registry executes each task type
        -- (runtime_background.py): general-queue types run in the worker,
        -- mail-queue types in mail dispatch. NULL is an unknown type.
        WHEN task_type IN (
            'activation_catchup',
            'automation_maintenance',
            'branding_cleanup',
            'campaign_boundary',
            'daily_digest_finalize',
            'daily_digest_prepare',
            'family_mail_prepare',
            'family_mail_test',
            'operational_collect',
            'operational_prepare',
            'operational_slack',
            'production_cleanup',
            'production_token_cleanup',
            'production_tokens',
            'report_exact_export',
            'report_export',
            'report_export_cleanup',
            'report_fact_verification',
            'report_facts',
            'security_prepare',
            'setup_finalize',
            'setup_source_cleanup',
            'setup_source_load',
            'source_refresh',
            'weekly_digest_finalize',
            'weekly_digest_prepare')
        THEN 'pk_stewardship_worker'
        WHEN task_type IN (
            'campaign_mail_test',
            'outbox_delivery',
            'setup_mail_test')
        THEN 'pk_stewardship_mail_dispatch'
    END $$;

-- Refuse to commit unless everything above is installed, so an upgrade
-- cannot report success with part of it missing.
DO $check$
BEGIN
    IF to_regclass('public.stewardship_automation_session') IS NULL
       OR to_regclass('public.stewardship_automation_login') IS NULL
       OR to_regclass('public.stewardship_automation_notice') IS NULL
       OR to_regclass('public.stewardship_automation_notice_ack') IS NULL THEN
        RAISE EXCEPTION 'The automation tables were not created';
    END IF;
    IF to_regprocedure('public.stewardship_automation_live_v1(uuid)') IS NULL
       OR to_regprocedure('public.stewardship_automation_fresh_v1(uuid,uuid)') IS NULL
       OR to_regprocedure('public.stewardship_admin_session_purge_v1(uuid[])') IS NULL THEN
        RAISE EXCEPTION 'The automation functions were not created';
    END IF;
    IF (SELECT count(*) FROM pg_trigger t
        WHERE NOT t.tgisinternal AND t.tgenabled='O' AND t.tgname IN (
            'stewardship_automation_session_mutable_guard_v1',
            'stewardship_automation_session_guard',
            'stewardship_automation_login_guard',
            'stewardship_automation_notice_immutable_guard_v1',
            'stewardship_automation_notice_insert_guard',
            'stewardship_automation_notice_ack_immutable_guard_v1',
            'stewardship_automation_notice_ack_insert_guard',
            'stewardship_portal_session_cleanup_guard'))<>8 THEN
        RAISE EXCEPTION 'The automation guards were not installed';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conrelid='public.stewardship_ops_incident'::regclass
                     AND conname='ops_incident_kind'
                     AND pg_get_constraintdef(oid) LIKE '%automation_refused%') THEN
        RAISE EXCEPTION 'ops_incident_kind does not list the automation kinds';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                   WHERE n.nspname='public' AND p.proname='stewardship_ops_incident_state_v1'
                     AND p.prosrc LIKE '%automation_policy_change%')
       OR NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                   WHERE n.nspname='public' AND p.proname='stewardship_ops_content_v1'
                     AND p.prosrc LIKE '%No further automation events%')
       OR NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                   WHERE n.nspname='public' AND p.proname='stewardship_task_type_login_v1'
                     AND p.prosrc LIKE '%automation_maintenance%') THEN
        RAISE EXCEPTION 'The automation incident kinds or task type were not installed';
    END IF;
    -- CREATE OR REPLACE FUNCTION resets every attribute it does not state,
    -- including SECURITY DEFINER set by a separate ALTER FUNCTION. The three
    -- replaced functions are invoker's rights with a fixed search_path in
    -- the baseline, as the new ones are except the purge function, which is
    -- the only definer here.
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public'
          AND p.proname IN ('stewardship_ops_incident_state_v1',
                            'stewardship_ops_content_v1',
                            'stewardship_task_type_login_v1',
                            'stewardship_automation_live_v1',
                            'stewardship_automation_fresh_v1',
                            'stewardship_admin_session_purge_v1')
          AND p.prosecdef = (p.proname = 'stewardship_admin_session_purge_v1')
          AND p.proconfig @> ARRAY['search_path=pg_catalog, public, pg_temp'])<>6 THEN
        RAISE EXCEPTION 'An automation function has the wrong security or search_path';
    END IF;
END
$check$;
