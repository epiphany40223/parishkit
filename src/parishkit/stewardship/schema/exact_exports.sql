-- Fresh-install exact request owner. Input protection exists before any builder.
CREATE TABLE stewardship_exact_export_request (
    id uuid PRIMARY KEY, created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
    actor_id uuid, correlation_id uuid NOT NULL,
    campaign_id uuid NOT NULL REFERENCES stewardship_campaign(id) DEFERRABLE INITIALLY DEFERRED,
    requester_id uuid NOT NULL, request_key uuid NOT NULL,
    task_id uuid NOT NULL UNIQUE REFERENCES stewardship_task_run(id) DEFERRABLE INITIALLY DEFERRED,
    configuration_id uuid NOT NULL REFERENCES stewardship_configuration_version(id) DEFERRABLE INITIALLY DEFERRED,
    population_scope varchar(12) NOT NULL,
    source_id uuid NOT NULL REFERENCES stewardship_source_snapshot(id) DEFERRABLE INITIALLY DEFERRED,
    submission_watermark bigint NOT NULL CHECK(submission_watermark>=0),
    timezone_configuration_id uuid NOT NULL REFERENCES stewardship_campaign_configuration(id) DEFERRABLE INITIALLY DEFERRED,
    through_date date NOT NULL, format varchar(4) NOT NULL, browser_timezone varchar(254) NOT NULL,
    CONSTRAINT exact_export_replay UNIQUE(requester_id,request_key),
    CONSTRAINT exact_export_scope CHECK(population_scope::text = ANY(ARRAY['historical'::varchar::text,'current'::varchar::text])),
    CONSTRAINT exact_export_format CHECK(format::text = ANY(ARRAY['csv'::varchar::text,'png'::varchar::text,'pdf'::varchar::text,'xlsx'::varchar::text]))
);
CREATE INDEX exact_export_correlation ON stewardship_exact_export_request(correlation_id);
CREATE INDEX exact_export_campaign ON stewardship_exact_export_request(campaign_id);
CREATE INDEX exact_export_configuration ON stewardship_exact_export_request(configuration_id);
CREATE INDEX exact_export_source ON stewardship_exact_export_request(source_id);
CREATE INDEX exact_export_timezone ON stewardship_exact_export_request(timezone_configuration_id);
CREATE TABLE stewardship_exact_export_cancel (
    id uuid PRIMARY KEY, created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
    actor_id uuid, correlation_id uuid NOT NULL,
    request_id uuid NOT NULL UNIQUE REFERENCES stewardship_exact_export_request(id) DEFERRABLE INITIALLY DEFERRED
);
CREATE INDEX exact_cancel_correlation ON stewardship_exact_export_cancel(correlation_id);
CREATE TABLE stewardship_exact_export_resolution (
    id uuid PRIMARY KEY, created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
    actor_id uuid, correlation_id uuid NOT NULL,
    request_id uuid NOT NULL UNIQUE REFERENCES stewardship_exact_export_request(id) DEFERRABLE INITIALLY DEFERRED,
    export_id uuid NOT NULL UNIQUE REFERENCES stewardship_export_request(id) DEFERRABLE INITIALLY DEFERRED,
    fact_set_id uuid NOT NULL REFERENCES stewardship_daily_fact_set(id) DEFERRABLE INITIALLY DEFERRED,
    run_id uuid NOT NULL REFERENCES stewardship_task_run(id) DEFERRABLE INITIALLY DEFERRED,
    fence bigint NOT NULL CHECK(fence>=0), worker_id uuid NOT NULL
);
CREATE INDEX exact_resolution_correlation ON stewardship_exact_export_resolution(correlation_id);
CREATE INDEX exact_resolution_facts ON stewardship_exact_export_resolution(fact_set_id);
CREATE INDEX exact_resolution_run ON stewardship_exact_export_resolution(run_id);

CREATE FUNCTION stewardship_exact_matches_v1(request_uuid uuid, fact_uuid uuid)
RETURNS boolean LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT EXISTS(SELECT 1 FROM stewardship_exact_export_request r
        JOIN stewardship_daily_fact_set f ON f.id=fact_uuid
        WHERE r.id=request_uuid AND
          ROW(r.campaign_id,r.population_scope,r.source_id,r.submission_watermark,
              r.timezone_configuration_id,r.through_date)=
          ROW(f.campaign_id,f.population_scope,f.source_id,f.submission_watermark,
              f.timezone_configuration_id,f.through_date))
$$;

CREATE FUNCTION stewardship_exact_claimable_v1(request_uuid uuid)
RETURNS boolean LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT EXISTS(SELECT 1 FROM stewardship_exact_export_request r WHERE r.id=request_uuid
      -- Root claiming commits before generation allocation. Serialize identical
      -- exact keys even during that gap, without assuming broker FIFO ordering.
      AND NOT EXISTS(SELECT 1 FROM stewardship_exact_export_request other
        JOIN stewardship_task_run t ON t.root_id=other.task_id
        WHERE other.task_id<>r.task_id AND t.state='running'
          AND ROW(other.campaign_id,other.population_scope,other.source_id,
              other.submission_watermark,other.timezone_configuration_id,other.through_date)=
            ROW(r.campaign_id,r.population_scope,r.source_id,
              r.submission_watermark,r.timezone_configuration_id,r.through_date))
      -- Do not overtake an ordinary owner that has claimed but not frozen yet.
      AND NOT EXISTS(SELECT 1 FROM stewardship_fact_demand d
        JOIN stewardship_task_run t ON t.domain_request_id=d.id AND t.task_type='report_facts'
        WHERE d.campaign_id=r.campaign_id AND d.population_scope=r.population_scope
          AND t.state='running' AND d.claimed_generation_id IS NULL)
      AND NOT EXISTS(SELECT 1 FROM stewardship_daily_fact_set f
        JOIN stewardship_task_run owner ON owner.id=f.task_id
        WHERE ROW(r.campaign_id,r.population_scope,r.source_id,r.submission_watermark,
                r.timezone_configuration_id,r.through_date)=
            ROW(f.campaign_id,f.population_scope,f.source_id,f.submission_watermark,
                f.timezone_configuration_id,f.through_date) AND f.state<>'ready'
          AND owner.root_id<>r.task_id
          AND EXISTS(SELECT 1 FROM stewardship_task_run live
            WHERE live.root_id=owner.root_id AND live.state IN ('queued','running','retry_wait','abandoned'))))
$$;

CREATE FUNCTION stewardship_exact_priority_v1(campaign_uuid uuid, scope_name text)
RETURNS boolean LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT EXISTS(SELECT 1 FROM stewardship_exact_export_request r
        JOIN stewardship_task_run t ON t.root_id=r.task_id
        WHERE r.campaign_id=campaign_uuid AND r.population_scope=scope_name
          AND t.state IN ('queued','retry_wait','running') AND t.not_before<=statement_timestamp()
          AND stewardship_export_authorized_v1(r.requester_id)
          AND stewardship_export_admitted_v1(r.campaign_id,true)
          AND NOT EXISTS(SELECT 1 FROM stewardship_exact_export_cancel WHERE request_id=r.id)
          AND NOT EXISTS(SELECT 1 FROM stewardship_exact_export_resolution WHERE request_id=r.id)
          AND stewardship_exact_claimable_v1(r.id))
$$;

CREATE FUNCTION stewardship_exact_request_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(736220,1);
    IF NEW.actor_id IS DISTINCT FROM NEW.requester_id
      OR NOT stewardship_export_authorized_v1(NEW.requester_id)
      OR NOT stewardship_export_admitted_v1(NEW.campaign_id,true)
      OR NOT EXISTS(SELECT 1 FROM stewardship_system_configuration
        WHERE active_configuration_id=NEW.configuration_id)
      OR NOT EXISTS(SELECT 1 FROM stewardship_campaign c
        JOIN stewardship_campaign_configuration p ON p.id=c.active_configuration_id
        JOIN stewardship_source_current s ON s.snapshot_id=NEW.source_id
        WHERE c.id=NEW.campaign_id AND p.id=NEW.timezone_configuration_id
          AND NEW.through_date=least(p.end_date,
            (stewardship_campaign_now_v1() AT TIME ZONE p.timezone)::date))
      OR NEW.submission_watermark<>coalesce((SELECT max(campaign_sequence)
        FROM stewardship_submission WHERE campaign_id=NEW.campaign_id AND mode='live'),0)
      OR NOT EXISTS(SELECT 1 FROM pg_timezone_names
        WHERE name=stewardship_timezone_name_v1(NEW.browser_timezone))
      OR NOT EXISTS(SELECT 1 FROM stewardship_task_run WHERE id=NEW.task_id
        AND root_id=id AND task_type='report_exact_export' AND domain_request_id=NEW.id
        AND initiated_by_id=NEW.requester_id AND idempotency_key=NEW.id::text AND state='queued')
    THEN RAISE EXCEPTION 'Exact export lacks frozen authorized input/task binding'
        USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;

CREATE FUNCTION stewardship_exact_request_commit_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF NEW.population_scope='current' AND NOT EXISTS(SELECT 1 FROM stewardship_source_pin
        WHERE snapshot_id=NEW.source_id AND parent_kind='report' AND parent_id=NEW.id
          AND expires_at IS NULL) THEN
        RAISE EXCEPTION 'Exact export requires its retained source pin' USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END $$;

CREATE FUNCTION stewardship_exact_cancel_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE request stewardship_exact_export_request%ROWTYPE;
BEGIN
    PERFORM pg_advisory_xact_lock(736220,1);
    SELECT * INTO request FROM stewardship_exact_export_request WHERE id=NEW.request_id;
    IF request.id IS NULL OR NOT stewardship_export_admitted_v1(request.campaign_id,true)
       OR NOT stewardship_export_authorized_v1(NEW.actor_id)
       OR (NEW.actor_id<>request.requester_id AND NOT stewardship_export_authorized_v1(NEW.actor_id,true))
       OR EXISTS(SELECT 1 FROM stewardship_exact_export_resolution WHERE request_id=request.id)
    THEN RAISE EXCEPTION 'Exact export cancellation is not admitted' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;

CREATE FUNCTION stewardship_exact_resolution_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE request stewardship_exact_export_request%ROWTYPE;
BEGIN
    PERFORM pg_advisory_xact_lock(736220,1);
    SELECT * INTO request FROM stewardship_exact_export_request WHERE id=NEW.request_id;
    IF request.id IS NULL OR NOT stewardship_export_admitted_v1(request.campaign_id,true)
      OR NOT stewardship_export_authorized_v1(request.requester_id)
      OR NEW.actor_id IS DISTINCT FROM NEW.worker_id
      OR NOT stewardship_fact_live(NEW.run_id,NEW.fence,NEW.worker_id)
      OR NOT EXISTS(SELECT 1 FROM stewardship_task_run WHERE id=NEW.run_id
          AND root_id=request.task_id AND task_type='report_exact_export' AND domain_request_id=request.id)
      OR EXISTS(SELECT 1 FROM stewardship_exact_export_cancel WHERE request_id=request.id)
      OR NOT stewardship_exact_matches_v1(request.id,NEW.fact_set_id)
      OR NOT EXISTS(SELECT 1 FROM stewardship_daily_fact_set WHERE id=NEW.fact_set_id AND state='ready')
    THEN RAISE EXCEPTION 'Exact resolution lacks owned ready input' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;

CREATE FUNCTION stewardship_exact_handoff_v1(export_uuid uuid, live boolean)
RETURNS boolean LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT EXISTS(SELECT 1 FROM stewardship_exact_export_resolution x
      JOIN stewardship_exact_export_request r ON r.id=x.request_id
      JOIN stewardship_export_request e ON e.id=x.export_id
      JOIN stewardship_task_run t ON t.id=x.run_id
      WHERE x.export_id=export_uuid AND x.fact_set_id=e.fact_set_id
        AND ROW(e.campaign_id,e.requester_id,e.request_key,e.configuration_id,e.format,e.browser_timezone)=
            ROW(r.campaign_id,r.requester_id,e.id,r.configuration_id,r.format,r.browser_timezone)
        AND t.root_id=r.task_id AND t.task_type='report_exact_export' AND t.domain_request_id=r.id
        AND (CASE WHEN live THEN stewardship_fact_live(x.run_id,x.fence,x.worker_id)
             ELSE t.state='succeeded' END))
$$;

CREATE FUNCTION stewardship_exact_resolution_commit_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF NOT stewardship_exact_handoff_v1(NEW.export_id,false) THEN
        RAISE EXCEPTION 'Exact handoff must complete its parent and pinned export atomically' USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END $$;

CREATE TRIGGER exact_request_insert BEFORE INSERT ON stewardship_exact_export_request
FOR EACH ROW EXECUTE FUNCTION stewardship_exact_request_guard_v1();
CREATE CONSTRAINT TRIGGER exact_request_complete AFTER INSERT ON stewardship_exact_export_request
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION stewardship_exact_request_commit_v1();
CREATE TRIGGER exact_cancel_insert BEFORE INSERT ON stewardship_exact_export_cancel
FOR EACH ROW EXECUTE FUNCTION stewardship_exact_cancel_guard_v1();
CREATE TRIGGER exact_resolution_insert BEFORE INSERT ON stewardship_exact_export_resolution
FOR EACH ROW EXECUTE FUNCTION stewardship_exact_resolution_guard_v1();
CREATE CONSTRAINT TRIGGER exact_resolution_complete AFTER INSERT ON stewardship_exact_export_resolution
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION stewardship_exact_resolution_commit_v1();
DO $$ DECLARE name text; BEGIN
    FOREACH name IN ARRAY ARRAY['request','cancel','resolution'] LOOP
        EXECUTE format('CREATE TRIGGER exact_immutable BEFORE UPDATE OR DELETE ON %I '
            'FOR EACH ROW EXECUTE FUNCTION stewardship_export_immutable_v1()', 'stewardship_exact_export_' || name);
    END LOOP;
END $$;

CREATE FUNCTION stewardship_exact_source_pin_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF OLD.parent_kind='report' THEN
        IF EXISTS(SELECT 1 FROM stewardship_exact_export_request
            WHERE id=OLD.parent_id AND source_id=OLD.snapshot_id) THEN
            RAISE EXCEPTION 'Retained exact export still requires its source input' USING ERRCODE='23514';
        END IF;
    END IF;
    IF TG_OP='UPDATE' THEN RETURN NEW; END IF;
    RETURN OLD;
END $$;
CREATE TRIGGER exact_source_pin_retained BEFORE UPDATE OR DELETE ON stewardship_source_pin
FOR EACH ROW EXECUTE FUNCTION stewardship_exact_source_pin_guard_v1();

CREATE FUNCTION stewardship_exact_fact_pin_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF current_user='pk_stewardship_worker' AND NEW.parent_kind='digest' THEN
        IF NOT EXISTS(SELECT 1 FROM stewardship_daily_digest_ready r
            JOIN stewardship_daily_digest_snapshot s ON s.id=r.snapshot_id
            WHERE s.id=NEW.parent_id AND r.fact_set_id=NEW.fact_set_id
              AND stewardship_daily_digest_live_v1(s.preparation_id,r.run_id,r.fence,r.worker_id)) THEN
            RAISE EXCEPTION 'Daily fact protection requires its exact ready handoff' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    IF current_user='pk_stewardship_worker' AND (NEW.parent_kind<>'export'
        OR NOT stewardship_exact_handoff_v1(NEW.parent_id,true)
        OR NOT EXISTS(SELECT 1 FROM stewardship_export_request
            WHERE id=NEW.parent_id AND fact_set_id=NEW.fact_set_id)) THEN
        RAISE EXCEPTION 'Worker fact protection requires its exact export handoff' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER exact_fact_pin_insert BEFORE INSERT ON stewardship_fact_pin
FOR EACH ROW EXECUTE FUNCTION stewardship_exact_fact_pin_guard_v1();
