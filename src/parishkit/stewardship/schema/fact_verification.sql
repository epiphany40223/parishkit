-- Fresh-install scheduled checks; completed evidence does not pin derived rows.
CREATE TABLE stewardship_fact_verification_request (
    id uuid PRIMARY KEY, created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
    actor_id uuid, correlation_id uuid NOT NULL,
    campaign_id uuid NOT NULL REFERENCES stewardship_campaign(id) DEFERRABLE INITIALLY DEFERRED,
    task_id uuid NOT NULL UNIQUE REFERENCES stewardship_task_run(id) DEFERRABLE INITIALLY DEFERRED,
    fact_set_id uuid NOT NULL, scheduled_day date NOT NULL,
    population_scope varchar(12) NOT NULL, source_id uuid NOT NULL,
    source_generation bigint NOT NULL CHECK(source_generation>=0),
    submission_watermark bigint NOT NULL CHECK(submission_watermark>=0),
    timezone_configuration_id uuid NOT NULL, through_date date NOT NULL,
    CONSTRAINT fact_verification_day UNIQUE(fact_set_id,scheduled_day),
    CONSTRAINT fact_verification_scope CHECK(population_scope::text = ANY(ARRAY['historical'::varchar::text,'current'::varchar::text]))
);
CREATE INDEX fact_verification_correlation ON stewardship_fact_verification_request(correlation_id);
CREATE INDEX fact_verification_campaign ON stewardship_fact_verification_request(campaign_id);
CREATE TABLE stewardship_fact_verification_result (
    id uuid PRIMARY KEY, created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
    actor_id uuid, correlation_id uuid NOT NULL,
    request_id uuid NOT NULL UNIQUE REFERENCES stewardship_fact_verification_request(id) DEFERRABLE INITIALLY DEFERRED,
    run_id uuid NOT NULL REFERENCES stewardship_task_run(id) DEFERRABLE INITIALLY DEFERRED,
    fence bigint NOT NULL CHECK(fence>=0), worker_id uuid NOT NULL,
    outcome varchar(8) NOT NULL, differing_days integer NOT NULL CHECK(differing_days>=0),
    CONSTRAINT fact_verification_outcome CHECK(
        (differing_days=0 AND outcome='matched') OR (differing_days>0 AND outcome='drift'))
);
CREATE INDEX fact_verification_result_correlation ON stewardship_fact_verification_result(correlation_id);
CREATE INDEX fact_verification_result_run ON stewardship_fact_verification_result(run_id);

CREATE FUNCTION stewardship_verification_matches_v1(request_uuid uuid)
RETURNS boolean LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT EXISTS(SELECT 1 FROM stewardship_fact_verification_request r
      JOIN stewardship_daily_fact_set f ON f.id=r.fact_set_id AND f.state='ready'
      WHERE r.id=request_uuid AND
        ROW(r.campaign_id,r.population_scope,r.source_id,r.source_generation,
            r.submission_watermark,r.timezone_configuration_id,r.through_date)=
        ROW(f.campaign_id,f.population_scope,f.source_id,f.source_generation,
            f.submission_watermark,f.timezone_configuration_id,f.through_date))
$$;

CREATE FUNCTION stewardship_verification_request_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE facts stewardship_daily_fact_set%ROWTYPE;
BEGIN
    PERFORM pg_advisory_xact_lock(736220,1);
    -- The compactor takes the exclusive counterpart before deleting. No UPDATE
    -- grant on calculated rows is needed by the metadata-only scheduler.
    PERFORM pg_advisory_xact_lock_shared(736231,hashtext(NEW.fact_set_id::text));
    SELECT * INTO facts FROM stewardship_daily_fact_set WHERE id=NEW.fact_set_id AND state='ready';
    IF facts.id IS NULL OR NEW.actor_id IS NOT NULL
      OR stewardship_fact_disposable(NEW.fact_set_id)
      OR NOT stewardship_export_admitted_v1(NEW.campaign_id,true)
      OR NEW.scheduled_day<>(stewardship_campaign_now_v1() AT TIME ZONE 'UTC')::date
      OR ROW(NEW.campaign_id,NEW.population_scope,NEW.source_id,NEW.source_generation,
          NEW.submission_watermark,NEW.timezone_configuration_id,NEW.through_date) IS DISTINCT FROM
         ROW(facts.campaign_id,facts.population_scope,facts.source_id,facts.source_generation,
          facts.submission_watermark,facts.timezone_configuration_id,facts.through_date)
      OR NOT EXISTS(SELECT 1 FROM stewardship_task_run WHERE id=NEW.task_id
          AND root_id=id AND task_type='report_fact_verification' AND domain_request_id=NEW.id
          AND initiated_by_id IS NULL AND idempotency_key=NEW.id::text AND state='queued')
      OR EXISTS(SELECT 1 FROM stewardship_fact_verification_request r
          JOIN stewardship_task_run t ON t.root_id=r.task_id
          WHERE r.fact_set_id=NEW.fact_set_id AND t.state IN ('queued','running','retry_wait','abandoned'))
    THEN RAISE EXCEPTION 'Verification requires an exact admitted daily task binding' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;

CREATE FUNCTION stewardship_verification_task_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE request stewardship_fact_verification_request%ROWTYPE;
BEGIN
    IF NEW.task_type<>'report_fact_verification' THEN RETURN NULL; END IF;
    PERFORM pg_advisory_xact_lock(736220,1);
    SELECT * INTO request FROM stewardship_fact_verification_request
      WHERE id=NEW.domain_request_id AND task_id=NEW.root_id;
    IF request.id IS NULL THEN
      RAISE EXCEPTION 'Verification task requires its committed request' USING ERRCODE='23514';
    END IF;
    PERFORM pg_advisory_xact_lock_shared(736231,hashtext(request.fact_set_id::text));
    IF NOT stewardship_export_admitted_v1(request.campaign_id,true)
      OR NOT stewardship_verification_matches_v1(request.id)
      OR EXISTS(SELECT 1 FROM stewardship_fact_verification_result WHERE request_id=request.id)
      OR EXISTS(SELECT 1 FROM stewardship_fact_verification_request r
        JOIN stewardship_task_run t ON t.root_id=r.task_id
        WHERE r.fact_set_id=request.fact_set_id AND r.id<>request.id
          AND t.state IN ('queued','running','retry_wait','abandoned'))
    THEN RAISE EXCEPTION 'Verification retry requires its available unowned generation' USING ERRCODE='23514'; END IF;
    RETURN NULL;
END $$;

CREATE FUNCTION stewardship_verification_result_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE request stewardship_fact_verification_request%ROWTYPE;
BEGIN
    PERFORM pg_advisory_xact_lock(736220,1);
    SELECT * INTO request FROM stewardship_fact_verification_request WHERE id=NEW.request_id;
    IF request.id IS NULL OR NEW.actor_id IS DISTINCT FROM NEW.worker_id
      OR NOT stewardship_export_admitted_v1(request.campaign_id,true)
      OR NOT stewardship_verification_matches_v1(request.id)
      OR NOT stewardship_fact_live(NEW.run_id,NEW.fence,NEW.worker_id)
      OR NOT EXISTS(SELECT 1 FROM stewardship_task_run WHERE id=NEW.run_id
        AND root_id=request.task_id AND task_type='report_fact_verification'
        AND domain_request_id=request.id)
    THEN RAISE EXCEPTION 'Verification result requires its live fenced original owner' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;

CREATE FUNCTION stewardship_verification_result_commit_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM stewardship_task_run WHERE id=NEW.run_id AND state='succeeded') THEN
      RAISE EXCEPTION 'Verification result and acknowledgment must commit together' USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END $$;

CREATE TRIGGER verification_request_insert BEFORE INSERT ON stewardship_fact_verification_request
FOR EACH ROW EXECUTE FUNCTION stewardship_verification_request_guard_v1();
CREATE CONSTRAINT TRIGGER verification_task_insert AFTER INSERT ON stewardship_task_run
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION stewardship_verification_task_guard_v1();
CREATE TRIGGER verification_result_insert BEFORE INSERT ON stewardship_fact_verification_result
FOR EACH ROW EXECUTE FUNCTION stewardship_verification_result_guard_v1();
CREATE CONSTRAINT TRIGGER verification_result_complete AFTER INSERT ON stewardship_fact_verification_result
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION stewardship_verification_result_commit_v1();
CREATE TRIGGER verification_request_immutable BEFORE UPDATE OR DELETE ON stewardship_fact_verification_request
FOR EACH ROW EXECUTE FUNCTION stewardship_export_immutable_v1();
CREATE TRIGGER verification_result_immutable BEFORE UPDATE OR DELETE ON stewardship_fact_verification_result
FOR EACH ROW EXECUTE FUNCTION stewardship_export_immutable_v1();
