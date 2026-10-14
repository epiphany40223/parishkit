-- Frozen forward migration file 0087 (the repository-wide file sequence):
-- confirmed ParishSoft publication plans and their per-entity outcomes (#528,
-- step 5, DAT-09.01). This file is installed by its Django migration in the
-- responses app and must never change once released;
-- tests/stewardship/test_schema_migration_files.py pins its digest. It
-- replaces no function, so no earlier body is copied here. Reversing needs
-- its own forward migration.
--
-- Five new, migration-owned, append-only tables. None holds a Member value:
-- only identifiers, field names, versions, counts and SHA-256 hashes.
--
-- stewardship_publication_plan: one preflight's result (the data spec's
--   Review and publication, steps 1 and 7), written by the worker. Its guard
--   requires a current Administrator (the one who asked), a campaign that
--   admits changes and a promoted source snapshot of the expected
--   organization. The database stamps the plan's time with its own
--   transaction's start, so the entity and field guards can require that
--   both are written in the plan's own transaction, and stamps whether the
--   system is in Testing mode.
-- stewardship_publication_entity: one entity per plan, blocked or not, with
--   the hash of the contact payload read and of the payload planned (both or
--   neither; only when not blocked and something is written). A deferred
--   check requires its field rows to agree.
-- stewardship_publication_field: one proposal per entity at the version it
--   was planned from (equal to the proposal's version when inserted, which
--   it share-locks), with the planner's outcome, which must agree with the
--   proposal's handling, decision and execution. The version is what a
--   later worker re-checks: any decision or Administrator edit changes it.
-- stewardship_publication_confirmation: the Administrator's final
--   confirmation, at most one per plan, written by web. It requires a fresh
--   sign-in (as 0080's large-change acceptance does), a plan not from
--   Testing mode (publication stops after preflight there) and less than 60
--   minutes old, the counts shown equal to the plan's, every planned
--   proposal still at its recorded version (share-locked), and no proposal
--   the plan writes being written by another confirmed plan whose entity is
--   not finished. Confirmations are serialized by an advisory lock so two
--   cannot both see no overlap.
-- stewardship_publication_outcome: one write attempt on a confirmed entity
--   that has something to write, written by the worker at the next attempt
--   number, never after a published or invalidated outcome (so a success is
--   never replayed), and only under the live source mutation lease in its
--   publication phase at the fence the row names.
--
-- Deferred constraint triggers require each plan, confirmation and outcome's
-- audit event, which holds an outcome word and a count only. The worker
-- cannot read audit rows, so stewardship_publication_audited_v1 (SECURITY
-- DEFINER, EXECUTE for the worker alone, granted by runtime_grants) answers
-- only whether one exact system event of the two publication types is
-- recorded. Every other function is invoker's rights with a fixed
-- search_path. The file ends with a DO block that refuses to commit unless
-- all of this is installed as declared.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

CREATE TABLE "stewardship_publication_plan" ("id" uuid NOT NULL PRIMARY KEY, "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "actor_id" uuid NULL, "correlation_id" uuid NOT NULL, "campaign_id" uuid NOT NULL, "source_snapshot_id" uuid NOT NULL, "organization_id" bigint NOT NULL CHECK ("organization_id" >= 0), "testing" boolean NOT NULL, "request_key" uuid NOT NULL, CONSTRAINT "publication_plan_replay" UNIQUE ("actor_id", "request_key"), CONSTRAINT "publication_plan_actor" CHECK ("actor_id" IS NOT NULL));
CREATE TABLE "stewardship_publication_entity" ("id" uuid NOT NULL PRIMARY KEY, "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "actor_id" uuid NULL, "correlation_id" uuid NOT NULL, "plan_id" uuid NOT NULL, "entity_kind" varchar(20) NOT NULL, "entity_key" varchar(36) NOT NULL, "blocked" boolean NOT NULL, "read_sha256" varchar(64) NULL, "planned_sha256" varchar(64) NULL, CONSTRAINT "publication_entity_identity" UNIQUE ("plan_id", "entity_kind", "entity_key"), CONSTRAINT "publication_entity_shape" CHECK ((("entity_kind")::text = ANY (ARRAY[('member'::character varying)::text, ('family'::character varying)::text]) AND NOT ("entity_key" = ''))), CONSTRAINT "publication_entity_hashes" CHECK ((("planned_sha256" IS NULL AND "read_sha256" IS NULL) OR (NOT "blocked" AND "planned_sha256"::text ~ '^[0-9a-f]{64}$' AND "read_sha256"::text ~ '^[0-9a-f]{64}$'))));
CREATE TABLE "stewardship_publication_field" ("id" uuid NOT NULL PRIMARY KEY, "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "actor_id" uuid NULL, "correlation_id" uuid NOT NULL, "entity_id" uuid NOT NULL, "proposal_id" uuid NOT NULL, "proposal_version" bigint NOT NULL CHECK ("proposal_version" >= 0), "field" varchar(40) NOT NULL, "outcome" varchar(24) NOT NULL, CONSTRAINT "publication_field_identity" UNIQUE ("entity_id", "proposal_id"), CONSTRAINT "publication_field_shape" CHECK ((("outcome")::text = ANY (ARRAY[('write'::character varying)::text, ('already_in_parishsoft'::character varying)::text, ('conflict'::character varying)::text, ('cannot_check'::character varying)::text, ('not_approved'::character varying)::text, ('by_hand'::character varying)::text]) AND "proposal_version" >= 1)));
CREATE TABLE "stewardship_publication_confirmation" ("id" uuid NOT NULL PRIMARY KEY, "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "actor_id" uuid NULL, "correlation_id" uuid NOT NULL, "plan_id" uuid NOT NULL UNIQUE, "session_id" uuid NOT NULL, "authenticated_at" timestamp with time zone NOT NULL, "request_key" uuid NOT NULL, "write_entities" integer NOT NULL CHECK ("write_entities" >= 0), "write_fields" integer NOT NULL CHECK ("write_fields" >= 0), "blocked_entities" integer NOT NULL CHECK ("blocked_entities" >= 0), CONSTRAINT "publication_confirmation_replay" UNIQUE ("actor_id", "request_key"), CONSTRAINT "publication_confirmation_shape" CHECK (("actor_id" IS NOT NULL AND "write_entities" >= 1)));
CREATE TABLE "stewardship_publication_outcome" ("id" uuid NOT NULL PRIMARY KEY, "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "actor_id" uuid NULL, "correlation_id" uuid NOT NULL, "entity_id" uuid NOT NULL, "attempt" integer NOT NULL CHECK ("attempt" >= 0), "result" varchar(12) NOT NULL, "failure_kind" varchar(40) NOT NULL, "fence" bigint NOT NULL CHECK ("fence" >= 0), CONSTRAINT "publication_outcome_attempt" UNIQUE ("entity_id", "attempt"), CONSTRAINT "publication_outcome_shape" CHECK (("attempt" >= 1 AND "fence" >= 1 AND ("result")::text = ANY (ARRAY[('published'::character varying)::text, ('invalidated'::character varying)::text, ('failed'::character varying)::text]))), CONSTRAINT "publication_outcome_failure" CHECK ((("failure_kind"::text ~ '^[a-z][a-z0-9_]{0,39}$' AND "result" = 'failed') OR (NOT ("result" = 'failed') AND "failure_kind" = ''))));
ALTER TABLE "stewardship_publication_plan" ADD CONSTRAINT "stewardship_publicat_campaign_id_967c5d56_fk_stewardsh" FOREIGN KEY ("campaign_id") REFERENCES "stewardship_campaign" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_publication_plan" ADD CONSTRAINT "stewardship_publicat_source_snapshot_id_b642947d_fk_stewardsh" FOREIGN KEY ("source_snapshot_id") REFERENCES "stewardship_source_snapshot" ("id") DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX "stewardship_publication_plan_correlation_id_7bf63f65" ON "stewardship_publication_plan" ("correlation_id");
CREATE INDEX "stewardship_publication_plan_campaign_id_967c5d56" ON "stewardship_publication_plan" ("campaign_id");
CREATE INDEX "stewardship_publication_plan_source_snapshot_id_b642947d" ON "stewardship_publication_plan" ("source_snapshot_id");
ALTER TABLE "stewardship_publication_entity" ADD CONSTRAINT "stewardship_publicat_plan_id_9532dc38_fk_stewardsh" FOREIGN KEY ("plan_id") REFERENCES "stewardship_publication_plan" ("id") DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX "stewardship_publication_entity_correlation_id_6ef0d206" ON "stewardship_publication_entity" ("correlation_id");
CREATE INDEX "stewardship_publication_entity_plan_id_9532dc38" ON "stewardship_publication_entity" ("plan_id");
ALTER TABLE "stewardship_publication_field" ADD CONSTRAINT "stewardship_publicat_entity_id_706206a9_fk_stewardsh" FOREIGN KEY ("entity_id") REFERENCES "stewardship_publication_entity" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_publication_field" ADD CONSTRAINT "stewardship_publicat_proposal_id_48a8297f_fk_stewardsh" FOREIGN KEY ("proposal_id") REFERENCES "stewardship_proposed_change" ("id") DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX "stewardship_publication_field_correlation_id_4ce5324b" ON "stewardship_publication_field" ("correlation_id");
CREATE INDEX "stewardship_publication_field_entity_id_706206a9" ON "stewardship_publication_field" ("entity_id");
CREATE INDEX "stewardship_publication_field_proposal_id_48a8297f" ON "stewardship_publication_field" ("proposal_id");
ALTER TABLE "stewardship_publication_confirmation" ADD CONSTRAINT "stewardship_publicat_plan_id_e6ecab43_fk_stewardsh" FOREIGN KEY ("plan_id") REFERENCES "stewardship_publication_plan" ("id") DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX "stewardship_publication_confirmation_correlation_id_2d4d541f" ON "stewardship_publication_confirmation" ("correlation_id");
ALTER TABLE "stewardship_publication_outcome" ADD CONSTRAINT "stewardship_publicat_entity_id_be9c23f5_fk_stewardsh" FOREIGN KEY ("entity_id") REFERENCES "stewardship_publication_entity" ("id") DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX "stewardship_publication_outcome_correlation_id_7e5f4652" ON "stewardship_publication_outcome" ("correlation_id");
CREATE INDEX "stewardship_publication_outcome_entity_id_be9c23f5" ON "stewardship_publication_outcome" ("entity_id");

-- Whether one exact system audit event of a publication type is recorded.
-- The worker writes these events but cannot read audit rows back, so its
-- deferred checks ask here; it answers for the two publication event types
-- only, and nothing else.
CREATE FUNCTION public.stewardship_publication_audited_v1(event_name text, subject uuid, campaign uuid, expected jsonb) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT event_name IN ('census_publication_planned','census_publication_recorded')
       AND EXISTS(SELECT 1 FROM public.stewardship_audit_event a
           JOIN public.stewardship_audit_context c ON c.event_id=a.id
           WHERE a.event_type=event_name AND a.subject_id=subject
             AND a.campaign_reference=campaign AND a.actor_id IS NULL
             AND c.actor_kind='system' AND c.schema='action'
             AND c.context=expected)
$$;
ALTER FUNCTION public.stewardship_publication_audited_v1(text, uuid, uuid, jsonb) SECURITY DEFINER;
REVOKE ALL ON FUNCTION public.stewardship_publication_audited_v1(text, uuid, uuid, jsonb) FROM PUBLIC;

CREATE FUNCTION public.stewardship_publication_plan_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE snapshot_state text; snapshot_organization bigint;
BEGIN
    IF TG_OP<>'INSERT' THEN
        RAISE EXCEPTION 'Publication plans are immutable' USING ERRCODE='23514';
    END IF;
    SELECT state, organization_id INTO snapshot_state, snapshot_organization
        FROM public.stewardship_source_snapshot WHERE id=NEW.source_snapshot_id;
    IF NEW.actor_id IS NULL
       OR public.stewardship_export_authorized_v1(NEW.actor_id,true) IS NOT TRUE
       OR public.stewardship_export_admitted_v1(NEW.campaign_id,true) IS NOT TRUE
       OR snapshot_state IS DISTINCT FROM 'promoted'
       OR snapshot_organization IS DISTINCT FROM NEW.organization_id
    THEN RAISE EXCEPTION 'Publication plan lacks a current Administrator, campaign or promoted source'
        USING ERRCODE='23514'; END IF;
    -- Database-owned: the plan's time is its transaction's start, so its
    -- entities and fields are written only in that transaction, and Testing
    -- is the system mode now.
    NEW.created_at:=transaction_timestamp();
    NEW.testing:=coalesce((SELECT mode<>'production'
        FROM public.stewardship_system_configuration LIMIT 1),true);
    RETURN NEW;
END $$;

CREATE FUNCTION public.stewardship_publication_plan_effect_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF public.stewardship_publication_audited_v1('census_publication_planned',NEW.id,
           NEW.campaign_id,jsonb_build_object('outcome','succeeded','count',
           (SELECT count(*) FROM public.stewardship_publication_entity e
             WHERE e.plan_id=NEW.id))) IS NOT TRUE
    THEN RAISE EXCEPTION 'Publication plan requires its audit' USING ERRCODE='23514'; END IF;
    RETURN NULL;
END $$;

CREATE FUNCTION public.stewardship_publication_entity_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE plan_time timestamptz;
BEGIN
    IF TG_OP<>'INSERT' THEN
        RAISE EXCEPTION 'Publication plan entities are immutable' USING ERRCODE='23514';
    END IF;
    SELECT created_at INTO plan_time FROM public.stewardship_publication_plan
        WHERE id=NEW.plan_id;
    IF plan_time IS DISTINCT FROM transaction_timestamp() OR NEW.actor_id IS NOT NULL
    THEN RAISE EXCEPTION 'A publication plan''s entities are written with the plan'
        USING ERRCODE='23514'; END IF;
    NEW.created_at:=transaction_timestamp();
    RETURN NEW;
END $$;

CREATE FUNCTION public.stewardship_publication_entity_effect_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE total bigint; writes bigint; blocking bigint;
BEGIN
    SELECT count(*), count(*) FILTER (WHERE outcome='write'),
           count(*) FILTER (WHERE outcome IN ('conflict','cannot_check'))
      INTO total, writes, blocking
      FROM public.stewardship_publication_field WHERE entity_id=NEW.id;
    -- Blocked exactly when a field conflicts or cannot be checked; hashes
    -- exactly when it is not blocked and a field is written.
    IF total=0 OR NEW.blocked IS DISTINCT FROM (blocking>0)
       OR (NEW.planned_sha256 IS NOT NULL) IS DISTINCT FROM (writes>0 AND NOT NEW.blocked)
    THEN RAISE EXCEPTION 'Publication plan entity disagrees with its fields'
        USING ERRCODE='23514'; END IF;
    RETURN NULL;
END $$;

CREATE FUNCTION public.stewardship_publication_field_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE entity public.stewardship_publication_entity%ROWTYPE;
        plan_campaign uuid; plan_time timestamptz;
        proposal public.stewardship_proposed_change%ROWTYPE; response_campaign uuid;
        by_hand boolean; approved boolean; checkable boolean;
BEGIN
    IF TG_OP<>'INSERT' THEN
        RAISE EXCEPTION 'Publication plan fields are immutable' USING ERRCODE='23514';
    END IF;
    SELECT * INTO entity FROM public.stewardship_publication_entity WHERE id=NEW.entity_id;
    SELECT campaign_id, created_at INTO plan_campaign, plan_time
        FROM public.stewardship_publication_plan WHERE id=entity.plan_id;
    -- Share-locked, so no decision or edit lands before the plan commits.
    SELECT * INTO proposal FROM public.stewardship_proposed_change
        WHERE id=NEW.proposal_id FOR SHARE;
    SELECT campaign_id INTO response_campaign FROM public.stewardship_submission
        WHERE id=proposal.submission_id AND mode='live';
    -- The planner's rules (responses.publication.plan_entity): By hand for
    -- anything not an API-writable Member field; otherwise Not approved
    -- unless approved; an approved row reconciliation holds in conflict is
    -- a conflict whatever ParishSoft now holds.
    by_hand:=proposal.handling<>'api' OR entity.entity_kind<>'member';
    approved:=proposal.decision='approved';
    checkable:=NOT by_hand AND approved AND proposal.execution<>'conflict';
    IF entity.id IS NULL OR plan_time IS DISTINCT FROM transaction_timestamp()
       OR NEW.actor_id IS NOT NULL OR proposal.id IS NULL
       OR response_campaign IS DISTINCT FROM plan_campaign
       OR proposal.entity_kind IS DISTINCT FROM entity.entity_kind
       OR proposal.entity_key IS DISTINCT FROM entity.entity_key
       OR proposal.field IS DISTINCT FROM NEW.field
       OR proposal.version IS DISTINCT FROM NEW.proposal_version
       OR proposal.execution NOT IN ('pending','failed','conflict')
       OR (CASE NEW.outcome
            WHEN 'by_hand' THEN NOT by_hand
            WHEN 'not_approved' THEN by_hand OR approved
            WHEN 'conflict' THEN by_hand OR NOT approved
            ELSE NOT checkable END)
    THEN RAISE EXCEPTION 'Publication plan field differs from its census change'
        USING ERRCODE='23514'; END IF;
    NEW.created_at:=transaction_timestamp();
    RETURN NEW;
END $$;

CREATE FUNCTION public.stewardship_publication_confirmation_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE plan public.stewardship_publication_plan%ROWTYPE;
        writes bigint; fields bigint; blocked bigint;
BEGIN
    IF TG_OP<>'INSERT' THEN
        RAISE EXCEPTION 'Publication confirmations are immutable' USING ERRCODE='23514';
    END IF;
    -- One confirmation at a time, so two cannot both see no overlap.
    PERFORM pg_advisory_xact_lock(736241, 91);
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
        RAISE EXCEPTION 'Publishing to ParishSoft requires fresh Administrator authentication'
            USING ERRCODE='42501';
    END IF;
    SELECT * INTO plan FROM public.stewardship_publication_plan WHERE id=NEW.plan_id;
    -- In Testing mode publication stops after preflight.
    IF plan.id IS NULL OR plan.testing
       OR plan.created_at<=statement_timestamp()-interval '60 minutes'
       OR public.stewardship_export_admitted_v1(plan.campaign_id,true) IS NOT TRUE
    THEN RAISE EXCEPTION 'Publication plan is missing, from Testing mode or too old to confirm'
        USING ERRCODE='23514'; END IF;
    SELECT count(*) FILTER (WHERE e.planned_sha256 IS NOT NULL),
           count(*) FILTER (WHERE e.blocked)
      INTO writes, blocked
      FROM public.stewardship_publication_entity e WHERE e.plan_id=plan.id;
    SELECT count(*) INTO fields
      FROM public.stewardship_publication_field f
      JOIN public.stewardship_publication_entity e ON e.id=f.entity_id
     WHERE e.plan_id=plan.id AND e.planned_sha256 IS NOT NULL AND f.outcome='write';
    IF NEW.write_entities IS DISTINCT FROM writes OR NEW.write_fields IS DISTINCT FROM fields
       OR NEW.blocked_entities IS DISTINCT FROM blocked
    THEN RAISE EXCEPTION 'Publication confirmation counts differ from the plan'
        USING ERRCODE='23514'; END IF;
    -- Share-locked in key order, so no decision or edit lands before commit.
    PERFORM 1 FROM public.stewardship_proposed_change p
      JOIN public.stewardship_publication_field f ON f.proposal_id=p.id
      JOIN public.stewardship_publication_entity e ON e.id=f.entity_id
     WHERE e.plan_id=plan.id ORDER BY p.id FOR SHARE OF p;
    IF EXISTS(SELECT 1 FROM public.stewardship_publication_field f
          JOIN public.stewardship_publication_entity e ON e.id=f.entity_id
          JOIN public.stewardship_proposed_change p ON p.id=f.proposal_id
         WHERE e.plan_id=plan.id
           AND (p.version<>f.proposal_version
                OR p.execution NOT IN ('pending','failed','conflict')))
    THEN RAISE EXCEPTION 'A planned census change has changed since preflight'
        USING ERRCODE='23514'; END IF;
    -- No change this plan writes is still being written by another
    -- confirmed plan: that entity has no published or invalidated outcome.
    IF EXISTS(SELECT 1 FROM public.stewardship_publication_field f
          JOIN public.stewardship_publication_entity e ON e.id=f.entity_id
          JOIN public.stewardship_publication_field other_field
            ON other_field.proposal_id=f.proposal_id AND other_field.outcome='write'
          JOIN public.stewardship_publication_entity other
            ON other.id=other_field.entity_id AND other.plan_id<>plan.id
           AND other.planned_sha256 IS NOT NULL
          JOIN public.stewardship_publication_confirmation c ON c.plan_id=other.plan_id
         WHERE e.plan_id=plan.id AND e.planned_sha256 IS NOT NULL AND f.outcome='write'
           AND NOT EXISTS(SELECT 1 FROM public.stewardship_publication_outcome o
                WHERE o.entity_id=other.id AND o.result IN ('published','invalidated')))
    THEN RAISE EXCEPTION 'Another confirmed publication is still writing these changes'
        USING ERRCODE='23505'; END IF;
    -- Attribution time is database-owned.
    NEW.created_at:=statement_timestamp();
    RETURN NEW;
END $$;

CREATE FUNCTION public.stewardship_publication_confirmation_effect_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM public.stewardship_audit_event a
           JOIN public.stewardship_audit_context c ON c.event_id=a.id
           JOIN public.stewardship_publication_plan plan ON plan.id=NEW.plan_id
           WHERE a.event_type='census_publication_confirmed' AND a.subject_id=NEW.id
             AND a.actor_id=NEW.actor_id AND a.campaign_reference=plan.campaign_id
             AND c.actor_kind='portal_user' AND c.schema='action'
             AND c.context=jsonb_build_object('outcome','changed',
                 'count',NEW.write_entities))
    THEN RAISE EXCEPTION 'Publication confirmation requires its audit' USING ERRCODE='23514'; END IF;
    RETURN NULL;
END $$;

CREATE FUNCTION public.stewardship_publication_outcome_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE entity public.stewardship_publication_entity%ROWTYPE;
        latest public.stewardship_publication_outcome%ROWTYPE; fenced boolean;
BEGIN
    IF TG_OP<>'INSERT' THEN
        RAISE EXCEPTION 'Publication outcomes are immutable' USING ERRCODE='23514';
    END IF;
    SELECT * INTO entity FROM public.stewardship_publication_entity WHERE id=NEW.entity_id;
    SELECT * INTO latest FROM public.stewardship_publication_outcome
        WHERE entity_id=NEW.entity_id ORDER BY attempt DESC LIMIT 1;
    SELECT phase='publication' AND fence=NEW.fence AND owner_id IS NOT NULL
           AND expires_at>clock_timestamp()
      INTO fenced FROM public.stewardship_source_lease WHERE singleton;
    -- A concurrent attempt at the same number fails the attempt key.
    IF entity.id IS NULL OR entity.planned_sha256 IS NULL OR NEW.actor_id IS NOT NULL
       OR NOT EXISTS(SELECT 1 FROM public.stewardship_publication_confirmation c
            WHERE c.plan_id=entity.plan_id)
       OR NEW.attempt IS DISTINCT FROM coalesce(latest.attempt,0)+1
       OR coalesce(latest.result IN ('published','invalidated'),false)
       OR fenced IS NOT TRUE
    THEN RAISE EXCEPTION 'Publication outcome lacks a confirmed entity, attempt or fence'
        USING ERRCODE='23514'; END IF;
    NEW.created_at:=statement_timestamp();
    RETURN NEW;
END $$;

CREATE FUNCTION public.stewardship_publication_outcome_effect_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE campaign uuid; fields bigint;
BEGIN
    SELECT plan.campaign_id INTO campaign
      FROM public.stewardship_publication_entity e
      JOIN public.stewardship_publication_plan plan ON plan.id=e.plan_id
     WHERE e.id=NEW.entity_id;
    SELECT count(*) INTO fields FROM public.stewardship_publication_field
     WHERE entity_id=NEW.entity_id AND outcome='write';
    IF public.stewardship_publication_audited_v1('census_publication_recorded',NEW.id,
           campaign,jsonb_build_object('outcome',CASE NEW.result
               WHEN 'published' THEN 'succeeded' WHEN 'failed' THEN 'failed'
               ELSE 'cancelled' END,'count',fields)) IS NOT TRUE
    THEN RAISE EXCEPTION 'Publication outcome requires its audit' USING ERRCODE='23514'; END IF;
    RETURN NULL;
END $$;

CREATE TRIGGER stewardship_publication_plan_guard
    BEFORE INSERT OR UPDATE OR DELETE ON public.stewardship_publication_plan
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_publication_plan_guard_v1();
CREATE CONSTRAINT TRIGGER stewardship_publication_plan_effect
    AFTER INSERT ON public.stewardship_publication_plan DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_publication_plan_effect_v1();
CREATE TRIGGER stewardship_publication_entity_guard
    BEFORE INSERT OR UPDATE OR DELETE ON public.stewardship_publication_entity
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_publication_entity_guard_v1();
CREATE CONSTRAINT TRIGGER stewardship_publication_entity_effect
    AFTER INSERT ON public.stewardship_publication_entity DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_publication_entity_effect_v1();
CREATE TRIGGER stewardship_publication_field_guard
    BEFORE INSERT OR UPDATE OR DELETE ON public.stewardship_publication_field
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_publication_field_guard_v1();
CREATE TRIGGER stewardship_publication_confirmation_guard
    BEFORE INSERT OR UPDATE OR DELETE ON public.stewardship_publication_confirmation
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_publication_confirmation_guard_v1();
CREATE CONSTRAINT TRIGGER stewardship_publication_confirmation_effect
    AFTER INSERT ON public.stewardship_publication_confirmation DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_publication_confirmation_effect_v1();
CREATE TRIGGER stewardship_publication_outcome_guard
    BEFORE INSERT OR UPDATE OR DELETE ON public.stewardship_publication_outcome
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_publication_outcome_guard_v1();
CREATE CONSTRAINT TRIGGER stewardship_publication_outcome_effect
    AFTER INSERT ON public.stewardship_publication_outcome DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_publication_outcome_effect_v1();

-- Refuse to commit unless every table, trigger and function is installed as
-- declared. Missing objects are checked first, on their own, because
-- PostgreSQL does not short-circuit OR and the casts below would otherwise
-- fail with their own error.
DO $check$
DECLARE kind text;
BEGIN
    FOREACH kind IN ARRAY ARRAY['plan','entity','field','confirmation','outcome'] LOOP
        IF to_regclass('public.stewardship_publication_'||kind) IS NULL
           OR to_regprocedure('public.stewardship_publication_'||kind||'_guard_v1()') IS NULL THEN
            RAISE EXCEPTION 'Migration 0087 (publication plans) is not installed as declared';
        END IF;
        -- The guard, enabled, a BEFORE row trigger on INSERT, UPDATE and
        -- DELETE (history immutability), invoker's rights with a fixed
        -- search_path, refusing every operation but INSERT.
        IF NOT EXISTS(SELECT 1 FROM pg_trigger t
            WHERE t.tgrelid=('public.stewardship_publication_'||kind)::regclass
              AND t.tgname='stewardship_publication_'||kind||'_guard' AND t.tgenabled='O'
              AND t.tgfoid=('public.stewardship_publication_'||kind||'_guard_v1()')::regprocedure
              AND t.tgtype=31)
           OR NOT EXISTS(SELECT 1 FROM pg_proc p
            WHERE p.oid=('public.stewardship_publication_'||kind||'_guard_v1()')::regprocedure
              AND NOT p.prosecdef
              AND p.proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
              AND p.prosrc LIKE '%IF TG_OP<>''INSERT'' THEN%'
              AND p.prosrc LIKE '%RAISE EXCEPTION%')
           -- PUBLIC holds no privilege on the table (grantee 0 in its ACL).
           OR EXISTS(SELECT 1 FROM pg_class c, aclexplode(coalesce(c.relacl,
                  acldefault('r',c.relowner))) a
              WHERE c.oid=('public.stewardship_publication_'||kind)::regclass
                AND a.grantee=0) THEN
            RAISE EXCEPTION 'stewardship_publication_% is not guarded as declared', kind;
        END IF;
    END LOOP;
    FOREACH kind IN ARRAY ARRAY['plan','entity','confirmation','outcome'] LOOP
        IF to_regprocedure('public.stewardship_publication_'||kind||'_effect_v1()') IS NULL
           OR NOT EXISTS(SELECT 1 FROM pg_trigger t
            WHERE t.tgrelid=('public.stewardship_publication_'||kind)::regclass
              AND t.tgname='stewardship_publication_'||kind||'_effect' AND t.tgenabled='O'
              AND t.tgfoid=('public.stewardship_publication_'||kind||'_effect_v1()')::regprocedure
              AND t.tgdeferrable AND t.tginitdeferred)
           OR NOT EXISTS(SELECT 1 FROM pg_proc p
            WHERE p.oid=('public.stewardship_publication_'||kind||'_effect_v1()')::regprocedure
              AND NOT p.prosecdef
              AND p.proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
              AND p.prosrc LIKE '%RAISE EXCEPTION%') THEN
            RAISE EXCEPTION 'stewardship_publication_% lacks its deferred check', kind;
        END IF;
    END LOOP;
    -- The checks that carry the design: transaction-bound plan rows, the
    -- recorded proposal version, fresh sign-in, Testing and overlap refusal,
    -- the attempt order and the fence.
    IF NOT EXISTS(SELECT 1 FROM pg_proc WHERE proname='stewardship_publication_plan_guard_v1'
            AND prosrc LIKE '%NEW.created_at:=transaction_timestamp()%'
            AND prosrc LIKE '%public.stewardship_export_authorized_v1(NEW.actor_id,true)%')
       OR NOT EXISTS(SELECT 1 FROM pg_proc WHERE proname='stewardship_publication_field_guard_v1'
            AND prosrc LIKE '%proposal.version IS DISTINCT FROM NEW.proposal_version%'
            AND prosrc LIKE '%plan_time IS DISTINCT FROM transaction_timestamp()%'
            AND prosrc LIKE '%FOR SHARE%')
       OR NOT EXISTS(SELECT 1 FROM pg_proc WHERE proname='stewardship_publication_entity_guard_v1'
            AND prosrc LIKE '%plan_time IS DISTINCT FROM transaction_timestamp()%')
       OR NOT EXISTS(SELECT 1 FROM pg_proc WHERE proname='stewardship_publication_confirmation_guard_v1'
            AND prosrc LIKE '%pg_advisory_xact_lock(736241, 91)%'
            AND prosrc LIKE '%public.stewardship_automation_fresh_v1(login.id,NEW.actor_id)%'
            AND prosrc LIKE '%plan.testing%'
            AND prosrc LIKE '%p.version<>f.proposal_version%'
            AND prosrc LIKE '%other.plan_id<>plan.id%')
       OR NOT EXISTS(SELECT 1 FROM pg_proc WHERE proname='stewardship_publication_outcome_guard_v1'
            AND prosrc LIKE '%phase=''publication'' AND fence=NEW.fence%'
            AND prosrc LIKE '%coalesce(latest.result IN (''published'',''invalidated''),false)%') THEN
        RAISE EXCEPTION 'The publication plan guards lack their checks';
    END IF;
    -- The audit answer: SECURITY DEFINER, STABLE, fixed search_path, no
    -- PUBLIC EXECUTE, and only for the two publication event types.
    IF to_regprocedure('public.stewardship_publication_audited_v1(text,uuid,uuid,jsonb)') IS NULL
       OR NOT EXISTS(SELECT 1 FROM pg_proc p
        WHERE p.oid='public.stewardship_publication_audited_v1(text,uuid,uuid,jsonb)'::regprocedure
          AND p.prosecdef AND p.provolatile='s' AND NOT p.proretset
          AND p.prorettype='boolean'::regtype
          AND p.proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
          AND p.prosrc LIKE '%event_name IN (''census_publication_planned'',''census_publication_recorded'')%'
          AND p.prosrc LIKE '%c.actor_kind=''system''%')
       OR has_function_privilege('public',
            'public.stewardship_publication_audited_v1(text,uuid,uuid,jsonb)', 'EXECUTE') THEN
        RAISE EXCEPTION 'stewardship_publication_audited_v1 is not installed as declared';
    END IF;
    IF (SELECT count(*) FROM pg_constraint
        WHERE conname IN ('publication_plan_replay','publication_entity_identity',
                          'publication_entity_hashes','publication_field_identity',
                          'publication_confirmation_replay','publication_outcome_attempt',
                          'publication_outcome_failure')
          AND convalidated)<>7
       OR NOT EXISTS(SELECT 1 FROM pg_constraint
        WHERE conrelid='public.stewardship_publication_confirmation'::regclass
          AND contype='u' AND pg_get_constraintdef(oid)='UNIQUE (plan_id)') THEN
        RAISE EXCEPTION 'The publication plan keys were not created';
    END IF;
END
$check$;
