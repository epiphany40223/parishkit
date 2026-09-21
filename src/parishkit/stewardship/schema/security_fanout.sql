-- Frozen security-event cohorts and per-Administrator delivery intents. The
-- cohort copies the event's recorded recipients, the Administrators who
-- existed before the expansion, never the current grants.
CREATE TABLE "stewardship_security_cohort" (
    "id" uuid NOT NULL PRIMARY KEY,
    "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL,
    "actor_id" uuid NULL, "correlation_id" uuid NOT NULL,
    "event_id" uuid NOT NULL UNIQUE, "configuration_id" uuid NOT NULL,
    "parish_id" uuid NOT NULL, "mode" varchar(16) NOT NULL,
    "addresses" jsonb NOT NULL,
    "recipient_count" integer NOT NULL CHECK ("recipient_count" >= 0),
    "run_id" uuid NOT NULL,
    "fence" bigint NOT NULL CHECK ("fence" >= 0), "worker_id" uuid NOT NULL,
    CONSTRAINT "security_cohort_mode" CHECK ("mode"::text = ANY(ARRAY[('testing'::varchar)::text, ('production'::varchar)::text])),
    CONSTRAINT "security_cohort_counts" CHECK (("fence" >= 1 AND "recipient_count" >= 1))
);
CREATE TABLE "stewardship_security_recipient" (
    "id" uuid NOT NULL PRIMARY KEY,
    "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL,
    "actor_id" uuid NULL, "correlation_id" uuid NOT NULL,
    "cohort_id" uuid NOT NULL, "address" varchar(254) NOT NULL,
    "outbox_id" uuid NOT NULL UNIQUE,
    CONSTRAINT "security_recipient_address" UNIQUE ("cohort_id", "address")
);
ALTER TABLE "stewardship_security_cohort" ADD CONSTRAINT "stewardship_security_event_id_37e51f5e_fk_stewardsh" FOREIGN KEY ("event_id") REFERENCES "stewardship_policy_security_event" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_security_cohort" ADD CONSTRAINT "stewardship_security_configuration_id_4c2e2c29_fk_stewardsh" FOREIGN KEY ("configuration_id") REFERENCES "stewardship_configuration_version" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_security_cohort" ADD CONSTRAINT "stewardship_security_parish_id_efec64e2_fk_stewardsh" FOREIGN KEY ("parish_id") REFERENCES "stewardship_parish" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_security_cohort" ADD CONSTRAINT "stewardship_security_run_id_2fe614a0_fk_stewardsh" FOREIGN KEY ("run_id") REFERENCES "stewardship_task_run" ("id") DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX "stewardship_security_cohort_correlation_id_f3429669" ON "stewardship_security_cohort" ("correlation_id");
CREATE INDEX "stewardship_security_cohort_configuration_id_4c2e2c29" ON "stewardship_security_cohort" ("configuration_id");
CREATE INDEX "stewardship_security_cohort_parish_id_efec64e2" ON "stewardship_security_cohort" ("parish_id");
CREATE INDEX "stewardship_security_cohort_run_id_2fe614a0" ON "stewardship_security_cohort" ("run_id");
ALTER TABLE "stewardship_security_recipient" ADD CONSTRAINT "stewardship_security_cohort_id_b3150fb4_fk_stewardsh" FOREIGN KEY ("cohort_id") REFERENCES "stewardship_security_cohort" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_security_recipient" ADD CONSTRAINT "stewardship_security_outbox_id_bb41ee1f_fk_stewardsh" FOREIGN KEY ("outbox_id") REFERENCES "stewardship_outbox_message" ("id") DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX "stewardship_security_recipient_correlation_id_cf51b200" ON "stewardship_security_recipient" ("correlation_id");
CREATE INDEX "stewardship_security_recipient_cohort_id_b3150fb4" ON "stewardship_security_recipient" ("cohort_id");

-- The scheduler decides whether an event has anyone to tell from a count; it
-- holds no grant on the recorded addresses. The view runs as its owner.
CREATE VIEW stewardship_security_notifiable AS
    SELECT id, created_at, jsonb_array_length(recipients) AS recipient_count
    FROM stewardship_policy_security_event;

CREATE FUNCTION stewardship_security_cohort_immutable_v1()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Historical records are append-only' USING ERRCODE='23514';
END $$;
CREATE TRIGGER stewardship_security_cohort_immutable_guard_v1
BEFORE UPDATE OR DELETE ON stewardship_security_cohort
FOR EACH ROW EXECUTE FUNCTION stewardship_security_cohort_immutable_v1();
CREATE FUNCTION stewardship_security_recipient_immutable_v1()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Historical records are append-only' USING ERRCODE='23514';
END $$;
CREATE TRIGGER stewardship_security_recipient_immutable_guard_v1
BEFORE UPDATE OR DELETE ON stewardship_security_recipient
FOR EACH ROW EXECUTE FUNCTION stewardship_security_recipient_immutable_v1();

-- The cohort must be exactly the event's recorded recipients as a set, each
-- address once, under the fenced preparation Task that owns this event.
CREATE FUNCTION stewardship_security_cohort_binding_v1()
RETURNS trigger LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE actual jsonb;
BEGIN
    IF NOT EXISTS(SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
        AND mode='ExclusiveLock' AND granted)
      OR NOT EXISTS(SELECT 1 FROM stewardship_task_run t
        CROSS JOIN stewardship_system_configuration runtime
        JOIN stewardship_parish p ON p.configuration_id=runtime.active_configuration_id
        WHERE t.id=NEW.run_id AND t.id=NEW.correlation_id
          AND t.task_type='security_prepare' AND t.domain_request_id=NEW.event_id
          AND t.state='running' AND t.fence=NEW.fence AND t.worker_id=NEW.worker_id
          AND NEW.actor_id=NEW.worker_id AND t.lease_expires_at>clock_timestamp()
          AND NEW.configuration_id=runtime.active_configuration_id
          AND NEW.mode=runtime.mode AND NEW.parish_id=p.id)
      THEN RAISE EXCEPTION 'Security cohort requires current fenced ownership'
        USING ERRCODE='23514'; END IF;
    -- The same addresses, compared as sets so no collation or code-point
    -- ordering can disagree, and each exactly once.
    SELECT recipients INTO actual FROM stewardship_policy_security_event WHERE id=NEW.event_id;
    IF actual IS NULL OR jsonb_typeof(NEW.addresses)<>'array'
      OR NOT (NEW.addresses <@ actual AND actual <@ NEW.addresses)
      OR NEW.recipient_count<>jsonb_array_length(NEW.addresses)
      OR NEW.recipient_count<>(SELECT count(DISTINCT value)
          FROM jsonb_array_elements_text(NEW.addresses) AS value) THEN
        RAISE EXCEPTION 'Security cohort must match the event''s recorded recipients'
          USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER stewardship_security_cohort_binding BEFORE INSERT ON stewardship_security_cohort
FOR EACH ROW EXECUTE FUNCTION stewardship_security_cohort_binding_v1();
REVOKE ALL ON FUNCTION stewardship_security_cohort_binding_v1() FROM PUBLIC;

CREATE FUNCTION stewardship_security_recipient_binding_v1()
RETURNS trigger LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
      AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
      AND mode='ExclusiveLock' AND granted)
      OR NOT EXISTS(SELECT 1 FROM stewardship_security_cohort c
      JOIN stewardship_task_run captured ON captured.id=c.run_id
      JOIN stewardship_task_run t ON t.root_id=captured.root_id
      JOIN stewardship_outbox_message m ON m.id=NEW.outbox_id
      JOIN stewardship_outbox_render r ON r.id=m.render_id
      WHERE c.id=NEW.cohort_id AND c.addresses ? NEW.address
        AND t.id=NEW.correlation_id AND t.task_type='security_prepare'
        AND t.domain_request_id=c.event_id AND t.state='running'
        AND t.worker_id=NEW.actor_id AND t.lease_expires_at>clock_timestamp()
        AND m.correlation_id=t.id AND m.actor_id=t.worker_id
        AND m.semantic_key=NEW.id AND m.scope_id=c.parish_id
        AND m.mode=c.mode AND m.purpose='security_event' AND m.routing='operational'
        AND m.credential_namespace='none' AND m.campaign_id IS NULL
        AND m.family_id IS NULL AND m.state='pending' AND m.version=1
        AND r.intended_recipients=jsonb_build_array(NEW.address)
        AND r.routed_recipients=r.intended_recipients) THEN
      RAISE EXCEPTION 'Security recipient requires its atomic exact delivery'
        USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER stewardship_security_recipient_binding BEFORE INSERT ON stewardship_security_recipient
FOR EACH ROW EXECUTE FUNCTION stewardship_security_recipient_binding_v1();
REVOKE ALL ON FUNCTION stewardship_security_recipient_binding_v1() FROM PUBLIC;

CREATE FUNCTION stewardship_security_prepare_write_v1(relation_name text, proposed jsonb)
RETURNS boolean LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE t stewardship_task_run%ROWTYPE; c stewardship_security_cohort%ROWTYPE;
    m stewardship_outbox_message%ROWTYPE;
BEGIN
    IF relation_name NOT IN ('stewardship_outbox_message','stewardship_outbox_render','stewardship_outbox_event')
      OR NOT EXISTS(SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
        AND mode='ExclusiveLock' AND granted) THEN RETURN false; END IF;
    SELECT * INTO t FROM stewardship_task_run WHERE id=(proposed->>'correlation_id')::uuid;
    IF t.id IS NULL OR t.task_type<>'security_prepare' OR t.state<>'running'
      OR t.worker_id IS DISTINCT FROM (proposed->>'actor_id')::uuid
      OR t.lease_expires_at<=clock_timestamp() THEN RETURN false; END IF;
    SELECT cohort.* INTO c FROM stewardship_security_cohort cohort
      JOIN stewardship_task_run captured ON captured.id=cohort.run_id
      WHERE cohort.event_id=t.domain_request_id AND captured.root_id=t.root_id;
    IF c.id IS NULL THEN RETURN false; END IF;
    IF relation_name='stewardship_outbox_message' THEN
      RETURN proposed->>'purpose'='security_event' AND proposed->>'routing'='operational'
        AND proposed->>'credential_namespace'='none' AND proposed->>'campaign_id' IS NULL
        AND proposed->>'family_id' IS NULL AND (proposed->>'scope_id')::uuid=c.parish_id
        AND proposed->>'mode'=c.mode AND proposed->>'state'='pending'
        AND proposed->>'action'='created' AND proposed->>'version'='1';
    END IF;
    SELECT * INTO m FROM stewardship_outbox_message
      WHERE id=(proposed->>'message_id')::uuid AND correlation_id=t.id AND actor_id=t.worker_id
        AND purpose='security_event' AND scope_id=c.parish_id AND mode=c.mode
        AND state='pending' AND version=1;
    IF m.id IS NULL THEN RETURN false; END IF;
    IF relation_name='stewardship_outbox_render' THEN
      RETURN (proposed->>'id')::uuid=m.render_id
        AND c.addresses ? (proposed->'intended_recipients'->>0)
        AND stewardship_security_render_matches_v1(proposed,c.event_id,c.configuration_id,
          proposed->'intended_recipients'->>0,c.mode);
    END IF;
    RETURN proposed->>'version'='1' AND proposed->>'action'='created';
END $$;

CREATE FUNCTION stewardship_security_prepare_receipt_v1()
RETURNS trigger LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF NEW.purpose='security_event' AND EXISTS(SELECT 1 FROM stewardship_task_run
      WHERE id=NEW.correlation_id AND task_type='security_prepare')
      AND NOT EXISTS(SELECT 1 FROM stewardship_security_recipient recipient
        JOIN stewardship_security_cohort c ON c.id=recipient.cohort_id
        JOIN stewardship_task_run t ON t.id=NEW.correlation_id
        WHERE recipient.outbox_id=NEW.id AND recipient.id=NEW.semantic_key
          AND recipient.correlation_id=t.id AND c.event_id=t.domain_request_id) THEN
      RAISE EXCEPTION 'Security outbox requires its atomic recipient receipt'
        USING ERRCODE='23514'; END IF;
    RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER stewardship_security_prepare_receipt
AFTER INSERT ON stewardship_outbox_message DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION stewardship_security_prepare_receipt_v1();
REVOKE ALL ON FUNCTION stewardship_security_prepare_receipt_v1() FROM PUBLIC;
