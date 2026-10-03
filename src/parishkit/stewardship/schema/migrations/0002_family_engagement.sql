-- Frozen forward migration 0002 (campaigns): durable Family engagement for
-- response reporting (#477). This file is installed by
-- campaigns/migrations/0002_family_engagement.py and must never change once
-- released; tests/stewardship/test_schema_migration_files.py pins its digest.
-- A later schema change gets its own numbered file. The fresh-install
-- baseline (0001) does not install this file: a fresh install runs 0001 and
-- then this, and ends in the same catalog as an upgraded database.
--
-- One row per Family and mode holds the first sign-in ("link followed"),
-- first form issuance ("form opened") and first step past the welcome page,
-- the furthest form step reached and the last instant the Family was seen.
-- The Family session rows that used to be the only source are deleted an
-- hour after the last activity, and their presence column is the current
-- step, not the furthest. No answers, names or credentials are stored here.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;
CREATE TABLE "stewardship_family_engagement" ("id" uuid NOT NULL PRIMARY KEY, "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "actor_id" uuid NULL, "correlation_id" uuid NOT NULL, "updated_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "version" bigint NOT NULL CHECK ("version" >= 0), "family_id" uuid NOT NULL, "mode" varchar(4) NOT NULL, "rehearsal_epoch_id" uuid NULL, "first_link_at" timestamp with time zone NULL, "first_form_at" timestamp with time zone NULL, "first_progress_at" timestamp with time zone NULL, "furthest_section" varchar(24) NOT NULL, "furthest_at" timestamp with time zone NULL, "last_seen_at" timestamp with time zone NOT NULL);
-- DEFERRABLE INITIALLY DEFERRED as every Django foreign key here; the writers
-- SET CONSTRAINTS it IMMEDIATE for their transaction (campaigns/engagement.py).
ALTER TABLE "stewardship_family_engagement" ADD CONSTRAINT "stewardship_family_e_family_id_71a8a34d_fk_stewardsh" FOREIGN KEY ("family_id") REFERENCES "stewardship_family_campaign" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_family_engagement" ADD CONSTRAINT "stewardship_campaigns_familyengagement_positive_version" CHECK ("version" >= 1);
-- NULLS NOT DISTINCT: the live row (no rehearsal epoch) is as unique as a
-- Testing row, so the writers' ON CONFLICT finds it.
ALTER TABLE "stewardship_family_engagement" ADD CONSTRAINT "family_engagement_identity" UNIQUE NULLS NOT DISTINCT ("family_id", "mode", "rehearsal_epoch_id");
ALTER TABLE "stewardship_family_engagement" ADD CONSTRAINT "family_engagement_mode_epoch" CHECK (((((mode)::text = 'live'::text) AND (rehearsal_epoch_id IS NULL)) OR (((mode)::text = 'test'::text) AND (rehearsal_epoch_id IS NOT NULL))));
ALTER TABLE "stewardship_family_engagement" ADD CONSTRAINT "family_engagement_furthest_shape" CHECK ((((furthest_at IS NULL) AND ((furthest_section)::text = ''::text)) OR ((furthest_at IS NOT NULL) AND ((furthest_section)::text = ANY ((ARRAY['welcome'::character varying, 'census'::character varying, 'members'::character varying, 'ministry'::character varying, 'financial'::character varying, 'closing'::character varying, 'additional'::character varying, 'review'::character varying])::text[])))));
ALTER TABLE "stewardship_family_engagement" ADD CONSTRAINT "family_engagement_progress_shape" CHECK ((((first_progress_at IS NULL) AND ((furthest_section)::text = ANY ((ARRAY[''::character varying, 'welcome'::character varying])::text[]))) OR ((first_progress_at IS NOT NULL) AND (NOT ((furthest_section)::text = ANY ((ARRAY[''::character varying, 'welcome'::character varying])::text[]))))));
ALTER TABLE "stewardship_family_engagement" ADD CONSTRAINT "family_engagement_evidence" CHECK (((first_link_at IS NOT NULL) OR (first_form_at IS NOT NULL) OR (furthest_at IS NOT NULL)));
ALTER TABLE "stewardship_family_engagement" ADD CONSTRAINT "family_engagement_seen_last" CHECK ((("first_link_at" IS NULL OR "first_link_at" <= ("last_seen_at")) AND ("first_form_at" IS NULL OR "first_form_at" <= ("last_seen_at")) AND ("first_progress_at" IS NULL OR "first_progress_at" <= ("last_seen_at")) AND ("furthest_at" IS NULL OR "furthest_at" <= ("last_seen_at"))));
CREATE INDEX "stewardship_family_engagement_correlation_id_6c560b2b" ON "stewardship_family_engagement" ("correlation_id");
CREATE INDEX "stewardship_family_engagement_family_id_71a8a34d" ON "stewardship_family_engagement" ("family_id");
CREATE INDEX "family_engagement_link" ON "stewardship_family_engagement" ("mode", "first_link_at");

-- The form's step order, as campaigns/credential_models.py PRESENCE_SECTIONS
-- lists it (a test keeps the two in step). No section is 0; welcome is 1.
-- Invoker rights and PUBLIC EXECUTE on purpose: the Family web login's upsert
-- and the invoker-rights guard below both call it, and it reads no data.
CREATE FUNCTION public.stewardship_engagement_rank_v1(section text) RETURNS integer
LANGUAGE sql IMMUTABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT COALESCE(array_position(ARRAY['welcome','census','members','ministry','financial','closing','additional','review'], section), 0)
$$;

CREATE FUNCTION public.stewardship_family_engagement_mutable_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    -- Written in the quoted per-column form every generated mutable guard
    -- uses, which test_all_concrete_mutable_records_have_enabled_guard checks
    -- against the model's immutable fields.
    IF NEW."id" IS DISTINCT FROM OLD."id"
       OR NEW."created_at" IS DISTINCT FROM OLD."created_at"
       OR NEW."family_id" IS DISTINCT FROM OLD."family_id"
       OR NEW."mode" IS DISTINCT FROM OLD."mode"
       OR NEW."rehearsal_epoch_id" IS DISTINCT FROM OLD."rehearsal_epoch_id" THEN
        RAISE EXCEPTION 'Record identity and bindings are immutable' USING ERRCODE='23514';
    END IF;
    IF NEW.version IS DISTINCT FROM OLD.version + 1 THEN
        RAISE EXCEPTION 'Every update must advance the record version' USING ERRCODE='23514';
    END IF;
    NEW.updated_at:=statement_timestamp();
    RETURN NEW;
END $$;

-- Only the Family web login (sign-in, form issuance, presence heartbeat and
-- the backfill command, which runs under it) writes engagement; the schema
-- owner is exempt as in the cleanup command guard, so migrations and the
-- disposable test schema can write directly. Every instant only moves the
-- way the funnel needs: a first_* instant never later and never back to
-- unknown, the furthest step never back, last_seen_at never earlier.
CREATE FUNCTION public.stewardship_family_engagement_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF current_user<>'pk_stewardship_web' AND NOT pg_has_role(current_user,
        (SELECT nspowner FROM pg_namespace WHERE nspname='public'),'USAGE') THEN
        RAISE EXCEPTION 'Only the Family web login records engagement' USING ERRCODE='23514';
    END IF;
    IF NEW.last_seen_at>clock_timestamp() THEN
        RAISE EXCEPTION 'Engagement cannot be recorded in the future' USING ERRCODE='23514';
    END IF;
    -- A Testing row belongs to a rehearsal epoch of the Family's own campaign,
    -- so Production-transition cleanup selects it exactly (production.sql).
    IF NEW.mode='test' AND NOT EXISTS (SELECT 1 FROM public.stewardship_rehearsal_epoch e
        JOIN public.stewardship_family_campaign f ON f.campaign_id=e.campaign_id
        WHERE e.id=NEW.rehearsal_epoch_id AND f.id=NEW.family_id) THEN
        RAISE EXCEPTION 'Testing engagement requires its campaign rehearsal epoch' USING ERRCODE='23514';
    END IF;
    IF TG_OP='INSERT' THEN
        IF NEW.version<>1 THEN
            RAISE EXCEPTION 'A new engagement record starts at version 1' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    IF (OLD.first_link_at IS NOT NULL AND (NEW.first_link_at IS NULL OR NEW.first_link_at>OLD.first_link_at))
       OR (OLD.first_form_at IS NOT NULL AND (NEW.first_form_at IS NULL OR NEW.first_form_at>OLD.first_form_at))
       OR (OLD.first_progress_at IS NOT NULL AND (NEW.first_progress_at IS NULL OR NEW.first_progress_at>OLD.first_progress_at))
       OR public.stewardship_engagement_rank_v1(NEW.furthest_section)<public.stewardship_engagement_rank_v1(OLD.furthest_section)
       OR (public.stewardship_engagement_rank_v1(NEW.furthest_section)=public.stewardship_engagement_rank_v1(OLD.furthest_section)
           AND NEW.furthest_at IS DISTINCT FROM OLD.furthest_at)
       OR NEW.last_seen_at<OLD.last_seen_at THEN
        RAISE EXCEPTION 'Family engagement only moves forward' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;

-- Deletion is retention work, never an ordinary privilege: the journaled
-- Production-transition cleanup deletes an inventoried Testing row through
-- its checkpoint effect, and the invalidated-epoch response cleanup deletes
-- the rest of a rehearsal's Testing rows. Live rows stay with their campaign.
-- SECURITY DEFINER as the cleanup protect guard is, because the effect
-- predicate is private to the schema owner.
CREATE FUNCTION public.stewardship_family_engagement_retention_v1() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF public.stewardship_cleanup_effect_v1('engagement',OLD.id)
       OR public.stewardship_test_response_cleanup_v1(OLD.mode,OLD.rehearsal_epoch_id) THEN
        RETURN OLD;
    END IF;
    RAISE EXCEPTION 'Family engagement is retained outside owned cleanup' USING ERRCODE='23514';
END $$;

CREATE TRIGGER stewardship_family_engagement_mutable_guard_v1 BEFORE UPDATE ON public.stewardship_family_engagement
FOR EACH ROW EXECUTE FUNCTION public.stewardship_family_engagement_mutable_v1();
CREATE TRIGGER family_engagement_guard BEFORE INSERT OR UPDATE ON public.stewardship_family_engagement
FOR EACH ROW EXECUTE FUNCTION public.stewardship_family_engagement_guard_v1();
CREATE TRIGGER family_engagement_retention BEFORE DELETE ON public.stewardship_family_engagement
FOR EACH ROW EXECUTE FUNCTION public.stewardship_family_engagement_retention_v1();
CREATE TRIGGER production_cleanup_protect BEFORE DELETE ON public.stewardship_family_engagement
FOR EACH ROW EXECUTE FUNCTION public.stewardship_cleanup_protect_v1('engagement');
REVOKE ALL ON FUNCTION public.stewardship_family_engagement_mutable_v1() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.stewardship_family_engagement_guard_v1() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.stewardship_family_engagement_retention_v1() FROM PUBLIC;

-- Testing engagement rows are a new Production-transition cleanup category.
ALTER TABLE public.stewardship_production_target DROP CONSTRAINT production_target_category;
ALTER TABLE public.stewardship_production_target ADD CONSTRAINT production_target_category CHECK (category::text = ANY (ARRAY[
        ('baselines'::varchar)::text,('session_data'::varchar)::text,('family_sessions'::varchar)::text,
        ('ministry_requests'::varchar)::text,('occurrences'::varchar)::text,('occurrence_events'::varchar)::text,
        ('outbox_events'::varchar)::text,('outbox_messages'::varchar)::text,('outbox_renders'::varchar)::text,
        ('proposals'::varchar)::text,('rehearsal_credentials'::varchar)::text,('rehearsal_macs'::varchar)::text,
        ('schedule_fulfillments'::varchar)::text,('source_pins'::varchar)::text,
        ('submission_receipts'::varchar)::text,('submissions'::varchar)::text,
        ('prior_inventory_targets'::varchar)::text,
        ('daily_digest_recipients'::varchar)::text,('daily_digest_ready'::varchar)::text,
        ('daily_digest_snapshots'::varchar)::text,('daily_digest_fact_pins'::varchar)::text,
        ('recovery_replacements'::varchar)::text,
        ('weekly_digest_recipients'::varchar)::text,('weekly_digest_snapshots'::varchar)::text,
        ('engagement'::varchar)::text
    ]));

-- A sign-in or form issuance that could not record its engagement row keeps
-- a durable operational event (the request itself goes ahead).
ALTER TABLE public.stewardship_operational_log DROP CONSTRAINT operational_event_safe;
ALTER TABLE public.stewardship_operational_log ADD CONSTRAINT operational_event_safe CHECK (event IN (
        'configuration_rejected','configuration_digest_mismatch','startup_rejected',
        'startup_validated','request_completed','report_audit_failed','report_shaping_failed',
        'task_started','task_completed',
        'task_failed','fact_drift','unstructured_log_suppressed','authentication_limits_weakened',
        'installer_request_failed','source_refresh_invalid','source_member_unusable',
        'source_ministry_name_repaired','source_retention_skipped',
        'source_tenant_mismatch','source_destructive_change',
        'source_refresh_held','source_credential_failed','source_provider_failed',
        'mail_provider_failed',
        'due_work_lag',
        'credential_handoff_key_mismatch','setup_credential_staged','delivery_unknown',
        'setup_credential_scrubbed','campaign_boundary_lag','production_cleanup_failed',
        'authentication_health_observation_failed',
        'task_timed_out','helper_timed_out','work_budget_reached','task_lease_lost',
        'family_engagement_failed'));

-- The two cleanup functions that enumerate categories, exactly as the
-- fresh-install files cleanup.sql and production.sql define them at this
-- release (a test keeps the latest migration's copy equal to the baseline).
CREATE OR REPLACE FUNCTION public.stewardship_cleanup_relation_v1(target_category text)
RETURNS text LANGUAGE sql IMMUTABLE SET search_path TO pg_catalog, public, pg_temp AS $$
    SELECT CASE target_category
        WHEN 'baselines' THEN 'stewardship_family_form_baseline'
        WHEN 'family_sessions' THEN 'stewardship_family_session'
        WHEN 'ministry_requests' THEN 'stewardship_ministry_request'
        WHEN 'occurrences' THEN 'stewardship_schedule_occurrence'
        WHEN 'occurrence_events' THEN 'stewardship_occurrence_transition'
        WHEN 'outbox_events' THEN 'stewardship_outbox_event'
        WHEN 'outbox_messages' THEN 'stewardship_outbox_message'
        WHEN 'outbox_renders' THEN 'stewardship_outbox_render'
        WHEN 'proposals' THEN 'stewardship_proposed_change'
        WHEN 'rehearsal_credentials' THEN 'stewardship_rehearsal_credential'
        WHEN 'rehearsal_macs' THEN 'stewardship_rehearsal_code_mac'
        WHEN 'schedule_fulfillments' THEN 'stewardship_schedule_fulfillment'
        WHEN 'source_pins' THEN 'stewardship_source_pin'
        WHEN 'submission_receipts' THEN 'stewardship_submission_receipt'
        WHEN 'submissions' THEN 'stewardship_submission'
        WHEN 'prior_inventory_targets' THEN 'stewardship_production_target'
        WHEN 'daily_digest_recipients' THEN 'stewardship_daily_digest_recipient'
        WHEN 'daily_digest_ready' THEN 'stewardship_daily_digest_ready'
        WHEN 'daily_digest_snapshots' THEN 'stewardship_daily_digest_snapshot'
        WHEN 'daily_digest_fact_pins' THEN 'stewardship_fact_pin'
        WHEN 'recovery_replacements' THEN 'stewardship_recovery_replacement'
        WHEN 'weekly_digest_recipients' THEN 'stewardship_weekly_digest_recipient'
        WHEN 'weekly_digest_snapshots' THEN 'stewardship_weekly_digest_snapshot'
        WHEN 'engagement' THEN 'stewardship_family_engagement'
        ELSE NULL END
$$;

CREATE OR REPLACE FUNCTION public.stewardship_cleanup_inventory_v1(campaign_uuid uuid)
RETURNS TABLE(category text, target_id uuid)
LANGUAGE sql STABLE AS $$
    WITH epochs AS NOT MATERIALIZED (
        SELECT id FROM public.stewardship_rehearsal_epoch WHERE campaign_id=campaign_uuid
    ), responses AS NOT MATERIALIZED (
        SELECT id FROM public.stewardship_submission
        WHERE campaign_id=campaign_uuid AND mode='test'
          AND rehearsal_epoch_id IN (SELECT id FROM epochs)
    ), baselines AS NOT MATERIALIZED (
        SELECT b.id FROM public.stewardship_family_form_baseline b
        JOIN public.stewardship_family_campaign f ON f.id=b.family_id
        WHERE f.campaign_id=campaign_uuid AND b.mode='test'
          AND b.rehearsal_epoch_id IN (SELECT id FROM epochs)
    ), sessions AS NOT MATERIALIZED (
        SELECT s.id,s.session_id FROM public.stewardship_family_session s
        JOIN public.stewardship_family_campaign f ON f.id=s.family_id
        WHERE f.campaign_id=campaign_uuid AND s.mode='testing'
          AND s.rehearsal_epoch_id IN (SELECT id FROM epochs)
    ), messages AS NOT MATERIALIZED (
        SELECT id FROM public.stewardship_outbox_message
        WHERE campaign_id=campaign_uuid AND mode='testing' AND routing='testing_override'
    ), occurrences AS NOT MATERIALIZED (
        SELECT o.id FROM public.stewardship_schedule_occurrence o
        JOIN public.stewardship_schedule_definition d ON d.id=o.definition_id
        WHERE d.campaign_id=campaign_uuid AND o.mode='testing' AND o.routing='testing_override'
    ), credentials AS NOT MATERIALIZED (
        SELECT c.id FROM public.stewardship_rehearsal_credential c
        JOIN public.stewardship_family_campaign f ON f.id=c.family_id
        WHERE f.campaign_id=campaign_uuid AND c.epoch_id IN (SELECT id FROM epochs)
    ), digest_snapshots AS NOT MATERIALIZED (
        SELECT s.id FROM public.stewardship_daily_digest_snapshot s
        JOIN public.stewardship_daily_digest_preparation p ON p.id=s.preparation_id
        WHERE s.campaign_id=campaign_uuid AND p.mode='testing'
          AND p.rehearsal_epoch_id IN (SELECT id FROM epochs)
    ), digest_ready AS NOT MATERIALIZED (
        SELECT id FROM public.stewardship_daily_digest_ready
        WHERE snapshot_id IN (SELECT id FROM digest_snapshots)
    ), weekly_snapshots AS NOT MATERIALIZED (
        SELECT s.id FROM public.stewardship_weekly_digest_snapshot s
        JOIN public.stewardship_weekly_digest_preparation p ON p.id=s.preparation_id
        WHERE s.campaign_id=campaign_uuid AND p.mode='testing'
          AND p.rehearsal_epoch_id IN (SELECT id FROM epochs)
    )
    SELECT 'baselines',id FROM baselines
    UNION ALL SELECT 'family_sessions',id FROM sessions
    UNION ALL SELECT 'session_data',id FROM sessions WHERE session_id IS NOT NULL
    UNION ALL SELECT 'submissions',id FROM responses
    UNION ALL SELECT 'proposals',id FROM public.stewardship_proposed_change
        WHERE submission_id IN (SELECT id FROM responses)
    UNION ALL SELECT 'ministry_requests',id FROM public.stewardship_ministry_request
        WHERE submission_id IN (SELECT id FROM responses)
    UNION ALL SELECT 'submission_receipts',id FROM public.stewardship_submission_receipt
        WHERE submission_id IN (SELECT id FROM responses)
    UNION ALL SELECT 'source_pins',id FROM public.stewardship_source_pin
        WHERE (parent_kind='submission' AND parent_id IN (SELECT id FROM responses))
           OR (parent_kind='form_baseline' AND parent_id IN (SELECT id FROM baselines))
           OR (parent_kind='digest' AND parent_id IN (SELECT id FROM digest_snapshots))
    UNION ALL SELECT 'daily_digest_snapshots',id FROM digest_snapshots
    UNION ALL SELECT 'daily_digest_ready',id FROM digest_ready
    UNION ALL SELECT 'daily_digest_recipients',id FROM public.stewardship_daily_digest_recipient
        WHERE ready_id IN (SELECT id FROM digest_ready)
    UNION ALL SELECT 'daily_digest_fact_pins',id FROM public.stewardship_fact_pin
        WHERE parent_kind='digest' AND parent_id IN (SELECT id FROM digest_snapshots)
    UNION ALL SELECT 'weekly_digest_snapshots',id FROM weekly_snapshots
    UNION ALL SELECT 'weekly_digest_recipients',id FROM public.stewardship_weekly_digest_recipient
        WHERE snapshot_id IN (SELECT id FROM weekly_snapshots)
    UNION ALL SELECT 'occurrences',id FROM occurrences
    UNION ALL SELECT 'recovery_replacements',id FROM public.stewardship_recovery_replacement
        WHERE previous_id IN (SELECT id FROM occurrences)
          AND replacement_id IN (SELECT id FROM occurrences)
    UNION ALL SELECT 'occurrence_events',id FROM public.stewardship_occurrence_transition
        WHERE occurrence_id IN (SELECT id FROM occurrences)
    UNION ALL SELECT 'schedule_fulfillments',f.id FROM public.stewardship_schedule_fulfillment f
        JOIN public.stewardship_schedule_definition d ON d.id=f.definition_id
        WHERE d.campaign_id=campaign_uuid AND f.mode='testing'
          AND f.occurrence_id IN (SELECT id FROM occurrences)
    UNION ALL SELECT 'outbox_messages',id FROM messages
    UNION ALL SELECT 'outbox_renders',id FROM public.stewardship_outbox_render
        WHERE message_id IN (SELECT id FROM messages)
    UNION ALL SELECT 'outbox_events',id FROM public.stewardship_outbox_event
        WHERE message_id IN (SELECT id FROM messages)
    UNION ALL SELECT 'rehearsal_credentials',id FROM credentials
    UNION ALL SELECT 'rehearsal_macs',id FROM public.stewardship_rehearsal_code_mac
        WHERE credential_id IN (SELECT id FROM credentials) AND epoch_id IN (SELECT id FROM epochs)
    UNION ALL SELECT 'prior_inventory_targets',i.id FROM public.stewardship_production_target i
        JOIN public.stewardship_production_request r ON r.id=i.request_id
        WHERE r.campaign_id=campaign_uuid AND r.state='cancelled'
    -- Testing Family engagement (#477; the table arrives in campaigns 0002,
    -- after this file, which function-body deferral allows at installation).
    UNION ALL SELECT 'engagement',e.id FROM public.stewardship_family_engagement e
        JOIN public.stewardship_family_campaign f ON f.id=e.family_id
        WHERE f.campaign_id=campaign_uuid AND e.mode='test'
          AND e.rehearsal_epoch_id IN (SELECT id FROM epochs)
$$;

-- Self-check: refuse to finish unless everything above is in place.
DO $$
BEGIN
    IF to_regclass('public.stewardship_family_engagement') IS NULL
       OR (SELECT count(*) FROM pg_trigger WHERE tgrelid='public.stewardship_family_engagement'::regclass
           AND NOT tgisinternal)<>4
       OR public.stewardship_cleanup_relation_v1('engagement') IS DISTINCT FROM 'stewardship_family_engagement'
       OR public.stewardship_engagement_rank_v1('review')<>8
       OR position('engagement' IN pg_get_functiondef('public.stewardship_cleanup_inventory_v1(uuid)'::regprocedure))=0
       OR position('engagement' IN pg_get_constraintdef(
           (SELECT oid FROM pg_constraint WHERE conname='production_target_category')))=0
       OR position('family_engagement_failed' IN pg_get_constraintdef(
           (SELECT oid FROM pg_constraint WHERE conname='operational_event_safe')))=0 THEN
        RAISE EXCEPTION 'Migration 0002_family_engagement did not install completely';
    END IF;
END $$;
