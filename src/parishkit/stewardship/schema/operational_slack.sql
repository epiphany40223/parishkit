-- Independent operational Slack delivery uses the Task as its durable intent.
-- Only immutable, non-personal submission and outcome facts are retained here.
CREATE TABLE "stewardship_ops_slack_attempt" (
    "id" uuid NOT NULL PRIMARY KEY,
    "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL,
    "actor_id" uuid NULL, "correlation_id" uuid NOT NULL,
    "notice_id" uuid NOT NULL, "run_id" uuid NOT NULL,
    "fence" bigint NOT NULL CHECK ("fence" >= 0), "worker_id" uuid NOT NULL,
    "configuration_id" uuid NOT NULL, "channel_id" varchar(64) NOT NULL,
    "fingerprint" varchar(64) NOT NULL, "mode" varchar(16) NOT NULL,
    "deadline_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL,
    CONSTRAINT "ops_slack_fence" UNIQUE ("run_id", "fence"),
    CONSTRAINT "ops_slack_positive_fence" CHECK ("fence" >= 1),
    CONSTRAINT "ops_slack_mode" CHECK ("mode"::text = ANY(ARRAY[('testing'::varchar)::text, ('production'::varchar)::text])),
    CONSTRAINT "ops_slack_target_shape" CHECK (("channel_id"::text ~ '^[CG][A-Z0-9]{1,63}$' AND "fingerprint"::text ~ '^[0-9a-f]{64}$'))
);
CREATE TABLE "stewardship_ops_slack_result" (
    "id" uuid NOT NULL PRIMARY KEY,
    "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL,
    "actor_id" uuid NULL, "correlation_id" uuid NOT NULL,
    "attempt_id" uuid NOT NULL UNIQUE, "outcome" varchar(24) NOT NULL, "reason" varchar(8) NOT NULL,
    CONSTRAINT "ops_slack_outcome" CHECK ("outcome"::text = ANY(ARRAY[('accepted'::varchar)::text, ('not_sent'::varchar)::text, ('delivery_unknown'::varchar)::text])),
    CONSTRAINT "ops_slack_result_reason" CHECK (("reason"::text = 'provider'::text OR ("outcome"::text = 'delivery_unknown'::text AND "reason"::text = 'recovery'::text)))
);
ALTER TABLE "stewardship_ops_slack_attempt" ADD CONSTRAINT "stewardship_ops_slac_notice_id_33ca7585_fk_stewardsh" FOREIGN KEY ("notice_id") REFERENCES "stewardship_ops_notice" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_ops_slack_attempt" ADD CONSTRAINT "stewardship_ops_slac_run_id_e7aae523_fk_stewardsh" FOREIGN KEY ("run_id") REFERENCES "stewardship_task_run" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_ops_slack_attempt" ADD CONSTRAINT "stewardship_ops_slac_configuration_id_9c560f90_fk_stewardsh" FOREIGN KEY ("configuration_id") REFERENCES "stewardship_configuration_version" ("id") DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX "stewardship_ops_slack_attempt_correlation_id_a13fbe91" ON "stewardship_ops_slack_attempt" ("correlation_id");
CREATE INDEX "stewardship_ops_slack_attempt_notice_id_33ca7585" ON "stewardship_ops_slack_attempt" ("notice_id");
CREATE INDEX "stewardship_ops_slack_attempt_run_id_e7aae523" ON "stewardship_ops_slack_attempt" ("run_id");
CREATE INDEX "stewardship_ops_slack_attempt_configuration_id_9c560f90" ON "stewardship_ops_slack_attempt" ("configuration_id");
ALTER TABLE "stewardship_ops_slack_result" ADD CONSTRAINT "stewardship_ops_slac_attempt_id_2bbba347_fk_stewardsh" FOREIGN KEY ("attempt_id") REFERENCES "stewardship_ops_slack_attempt" ("id") DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX "stewardship_ops_slack_result_correlation_id_5e267609" ON "stewardship_ops_slack_result" ("correlation_id");

CREATE FUNCTION stewardship_ops_slack_write_v1()
RETURNS trigger LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE task stewardship_task_run%ROWTYPE; attempt stewardship_ops_slack_attempt%ROWTYPE;
BEGIN
    IF TG_OP<>'INSERT' THEN
      RAISE EXCEPTION 'Historical records are append-only' USING ERRCODE='23514';
    END IF;
    IF session_user<>'pk_stewardship_worker' OR NOT EXISTS(SELECT 1 FROM pg_locks
        WHERE pid=pg_backend_pid() AND locktype='advisory' AND classid=736220
          AND objid=1 AND objsubid=2 AND mode='ExclusiveLock' AND granted) THEN
      RAISE EXCEPTION 'Operational Slack requires ordered worker ownership' USING ERRCODE='23514';
    END IF;
    SELECT * INTO task FROM stewardship_task_run WHERE id=NEW.correlation_id;
    IF task.id IS NULL OR task.task_type<>'operational_slack'
      OR NOT EXISTS(SELECT 1 FROM stewardship_task_run root WHERE root.id=task.root_id
        AND root.task_type=task.task_type AND root.domain_request_id=task.domain_request_id
        AND root.idempotency_key=task.domain_request_id::text) THEN
      RAISE EXCEPTION 'Operational Slack task binding differs' USING ERRCODE='23514';
    END IF;
    IF TG_TABLE_NAME='stewardship_ops_slack_attempt' THEN
      IF task.id<>NEW.run_id OR task.domain_request_id<>NEW.notice_id
        OR task.state<>'running' OR task.fence<>NEW.fence
        OR task.worker_id IS DISTINCT FROM NEW.worker_id
        OR NEW.actor_id IS DISTINCT FROM task.worker_id
        OR task.lease_expires_at<=clock_timestamp()
        OR NOT EXISTS(SELECT 1 FROM stewardship_system_configuration runtime
          JOIN stewardship_applied_integration integration
            ON integration.configuration_id=runtime.active_configuration_id AND integration.kind='slack'
          WHERE NEW.configuration_id=runtime.active_configuration_id AND NEW.mode=runtime.mode
            AND NEW.channel_id=integration.settings->>'channel_id'
            AND NEW.fingerprint=integration.credential_fingerprint)
        OR EXISTS(SELECT 1 FROM stewardship_ops_slack_attempt a
          JOIN stewardship_task_run earlier ON earlier.id=a.run_id
          LEFT JOIN stewardship_ops_slack_result r ON r.attempt_id=a.id
          WHERE earlier.root_id=task.root_id AND (r.id IS NULL OR r.outcome<>'not_sent'))
        OR (SELECT count(*) FROM stewardship_ops_slack_attempt WHERE run_id=task.id)>=5 THEN
        RAISE EXCEPTION 'Operational Slack submission is not currently admitted' USING ERRCODE='23514';
      END IF;
      NEW.deadline_at:=clock_timestamp()+interval '35 seconds';
    ELSE
      SELECT * INTO attempt FROM stewardship_ops_slack_attempt WHERE id=NEW.attempt_id;
      IF attempt.id IS NULL OR attempt.run_id<>task.id
        OR attempt.notice_id<>task.domain_request_id
        OR NOT ((NEW.reason='provider' AND task.state='running'
            AND task.fence=attempt.fence AND task.worker_id=attempt.worker_id
            AND NEW.actor_id IS NOT DISTINCT FROM attempt.worker_id AND task.lease_expires_at>clock_timestamp())
          OR (NEW.reason='recovery' AND task.state='abandoned'
            AND task.fence>=attempt.fence AND attempt.deadline_at<=clock_timestamp()
            AND NEW.actor_id IS NOT NULL AND NEW.outcome='delivery_unknown')) THEN
        RAISE EXCEPTION 'Operational Slack result ownership differs' USING ERRCODE='23514';
      END IF;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER stewardship_ops_slack_attempt_write
BEFORE INSERT OR UPDATE OR DELETE ON stewardship_ops_slack_attempt
FOR EACH ROW EXECUTE FUNCTION stewardship_ops_slack_write_v1();
CREATE TRIGGER stewardship_ops_slack_result_write
BEFORE INSERT OR UPDATE OR DELETE ON stewardship_ops_slack_result
FOR EACH ROW EXECUTE FUNCTION stewardship_ops_slack_write_v1();
REVOKE ALL ON FUNCTION stewardship_ops_slack_write_v1() FROM PUBLIC;

CREATE FUNCTION stewardship_ops_slack_error_v1()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF NEW.outcome<>'accepted' THEN
      INSERT INTO stewardship_operational_log(id,actor_id,correlation_id,event,level,schema,context)
      VALUES(gen_random_uuid(),NEW.actor_id,NEW.correlation_id,'task_failed',
        CASE WHEN NEW.outcome='delivery_unknown' THEN 'WARNING' ELSE 'ERROR' END,
        'exception','{}'::jsonb);
    END IF;
    RETURN NULL;
END $$;
CREATE TRIGGER stewardship_ops_slack_error AFTER INSERT ON stewardship_ops_slack_result
FOR EACH ROW EXECUTE FUNCTION stewardship_ops_slack_error_v1();
REVOKE ALL ON FUNCTION stewardship_ops_slack_error_v1() FROM PUBLIC;

CREATE FUNCTION stewardship_ops_slack_task_error_v1()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    -- Failures before provider submission have no result row to log them.
    -- Alert failure stays ERROR, never recursively creates another CRITICAL.
    IF NEW.task_type='operational_slack' AND NEW.state='failed'
      AND OLD.state<>'failed' AND NOT EXISTS(
        SELECT 1 FROM stewardship_ops_slack_attempt a
        JOIN stewardship_ops_slack_result r ON r.attempt_id=a.id
        WHERE a.run_id=NEW.id AND r.outcome<>'accepted') THEN
      INSERT INTO stewardship_operational_log(id,actor_id,correlation_id,event,level,schema,context)
      VALUES(gen_random_uuid(),NEW.actor_id,NEW.correlation_id,'task_failed',
        'ERROR','exception','{}'::jsonb);
    END IF;
    RETURN NULL;
END $$;
CREATE TRIGGER stewardship_ops_slack_task_error AFTER UPDATE ON stewardship_task_run
FOR EACH ROW EXECUTE FUNCTION stewardship_ops_slack_task_error_v1();
REVOKE ALL ON FUNCTION stewardship_ops_slack_task_error_v1() FROM PUBLIC;
