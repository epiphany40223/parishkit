-- Completion proof retains only identities, never a copy of calculated rows.
-- No fact-set FK: finished work must not defeat derived-fact compaction.
CREATE TABLE public.stewardship_fact_build_receipt (
    id uuid PRIMARY KEY,
    created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
    actor_id uuid,
    correlation_id uuid NOT NULL,
    task_id uuid NOT NULL UNIQUE REFERENCES public.stewardship_task_run(id) DEFERRABLE INITIALLY DEFERRED,
    run_id uuid NOT NULL REFERENCES public.stewardship_task_run(id) DEFERRABLE INITIALLY DEFERRED,
    demand_id uuid NOT NULL REFERENCES public.stewardship_fact_demand(id) DEFERRABLE INITIALLY DEFERRED,
    revision bigint NOT NULL CHECK(revision>=0),
    task_fence bigint NOT NULL CHECK(task_fence>=0),
    worker_id uuid NOT NULL,
    fact_set_id uuid NOT NULL
);
CREATE INDEX fact_build_receipt_correlation ON public.stewardship_fact_build_receipt(correlation_id);
CREATE INDEX fact_build_receipt_run ON public.stewardship_fact_build_receipt(run_id);
CREATE INDEX fact_build_receipt_demand ON public.stewardship_fact_build_receipt(demand_id);

CREATE FUNCTION public.stewardship_fact_build_receipt_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE demand stewardship_fact_demand%ROWTYPE;
BEGIN
    IF TG_OP<>'INSERT' THEN
        RAISE EXCEPTION 'Fact completion evidence is immutable' USING ERRCODE='23514';
    END IF;
    SELECT * INTO demand FROM stewardship_fact_demand WHERE id=NEW.demand_id FOR UPDATE;
    IF NOT FOUND OR demand.claimed_task_id IS DISTINCT FROM NEW.run_id
        OR demand.claimed_task_fence IS DISTINCT FROM NEW.task_fence
        OR demand.claimed_worker_id IS DISTINCT FROM NEW.worker_id
        OR demand.claimed_revision IS DISTINCT FROM NEW.revision
        OR demand.claimed_generation_id IS DISTINCT FROM NEW.fact_set_id
        OR NOT stewardship_fact_live(NEW.run_id,NEW.task_fence,NEW.worker_id)
        OR NOT EXISTS(SELECT 1 FROM stewardship_task_run WHERE id=NEW.run_id
            AND root_id=NEW.task_id AND task_type='report_facts'
            AND domain_request_id=NEW.demand_id)
        OR NOT EXISTS(SELECT 1 FROM stewardship_daily_fact_set WHERE id=NEW.fact_set_id
            AND state='ready' AND campaign_id=demand.campaign_id
            AND population_scope=demand.population_scope) THEN
        RAISE EXCEPTION 'Fact completion lacks its exact owned ready generation' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER fact_build_receipt_guard BEFORE INSERT OR UPDATE OR DELETE
    ON public.stewardship_fact_build_receipt FOR EACH ROW
    EXECUTE FUNCTION public.stewardship_fact_build_receipt_guard_v1();

CREATE FUNCTION public.stewardship_fact_build_receipt_commit_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM stewardship_task_run WHERE id=NEW.run_id
            AND root_id=NEW.task_id AND state='succeeded')
        OR EXISTS(SELECT 1 FROM stewardship_fact_demand WHERE id=NEW.demand_id
            AND claimed_task_id=NEW.run_id) THEN
        RAISE EXCEPTION 'Fact completion must release demand and finish its task atomically'
            USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER fact_build_receipt_commit AFTER INSERT
    ON public.stewardship_fact_build_receipt DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_fact_build_receipt_commit_v1();

-- General-worker write authority cannot turn an unrelated task into a report
-- builder. Schema-owner fixtures still exercise the generic storage primitive.
CREATE FUNCTION public.stewardship_fact_runtime_binding_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE demand stewardship_fact_demand%ROWTYPE;
        owned_task stewardship_task_run%ROWTYPE;
        exact_request record;
BEGIN
    IF current_user<>'pk_stewardship_worker' THEN RETURN NEW; END IF;
    SELECT * INTO owned_task FROM stewardship_task_run WHERE id=NEW.task_id;
    IF owned_task.task_type='report_exact_export' THEN
        SELECT * INTO exact_request FROM stewardship_exact_export_request
            WHERE id=owned_task.domain_request_id AND task_id=owned_task.root_id;
        IF NOT FOUND OR ROW(NEW.campaign_id,NEW.population_scope,NEW.source_id,
            NEW.submission_watermark,NEW.timezone_configuration_id,NEW.through_date)
            IS DISTINCT FROM ROW(exact_request.campaign_id,exact_request.population_scope,
                exact_request.source_id,exact_request.submission_watermark,
                exact_request.timezone_configuration_id,exact_request.through_date)
            OR NOT stewardship_export_authorized_v1(exact_request.requester_id)
            OR NOT stewardship_export_admitted_v1(exact_request.campaign_id,true)
            OR EXISTS(SELECT 1 FROM stewardship_exact_export_cancel WHERE request_id=exact_request.id)
            OR EXISTS(SELECT 1 FROM stewardship_exact_export_resolution WHERE request_id=exact_request.id) THEN
            RAISE EXCEPTION 'Exact builder differs from its admitted frozen request' USING ERRCODE='23514';
        END IF;
        IF TG_OP='UPDATE' AND NOT EXISTS(SELECT 1 FROM stewardship_task_run
            WHERE id=OLD.task_id AND root_id=owned_task.root_id)
            AND NOT EXISTS(SELECT 1 FROM stewardship_task_run prior
                WHERE prior.id=OLD.task_id AND prior.task_type='report_exact_export'
                  AND NOT EXISTS(SELECT 1 FROM stewardship_task_run active
                    WHERE active.root_id=prior.root_id AND active.state IN ('queued','running','retry_wait','abandoned'))
                  AND NOT EXISTS(SELECT 1 FROM stewardship_fact_demand WHERE claimed_generation_id=OLD.id)) THEN
            RAISE EXCEPTION 'Exact recovery cannot replace a recoverable root' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    SELECT * INTO owned_task FROM stewardship_task_run
        WHERE id=NEW.task_id AND task_type='report_facts';
    SELECT * INTO demand FROM stewardship_fact_demand WHERE id=owned_task.domain_request_id;
    IF NOT FOUND OR demand.campaign_id<>NEW.campaign_id
        OR demand.population_scope<>NEW.population_scope THEN
        RAISE EXCEPTION 'Fact builder is not bound to this demand' USING ERRCODE='23514';
    END IF;
    IF TG_OP='INSERT' THEN
        IF demand.claimed_generation_id IS NOT NULL
            OR ROW(NEW.source_id,NEW.submission_watermark,NEW.timezone_configuration_id,NEW.through_date)
                IS DISTINCT FROM ROW(demand.requested_source_id,demand.requested_submission_watermark,
                    demand.requested_timezone_configuration_id,demand.requested_through_date)
            OR EXISTS(SELECT 1 FROM stewardship_daily_fact_set f
                JOIN stewardship_task_run t ON t.id=f.task_id WHERE t.root_id=owned_task.root_id) THEN
            RAISE EXCEPTION 'Fact allocation differs from its unclaimed window' USING ERRCODE='23514';
        END IF;
    ELSIF demand.claimed_generation_id IS NULL
        AND demand.pending_due_at<=clock_timestamp()
        AND ROW(NEW.source_id,NEW.submission_watermark,NEW.timezone_configuration_id,NEW.through_date)
            IS NOT DISTINCT FROM ROW(demand.requested_source_id,demand.requested_submission_watermark,
                demand.requested_timezone_configuration_id,demand.requested_through_date)
        AND EXISTS(SELECT 1 FROM stewardship_task_run prior
            WHERE prior.id=OLD.task_id AND prior.task_type='report_exact_export'
              AND NOT EXISTS(SELECT 1 FROM stewardship_task_run active
                WHERE active.root_id=prior.root_id AND active.state IN ('queued','running','retry_wait','abandoned'))
              AND NOT EXISTS(SELECT 1 FROM stewardship_fact_demand WHERE claimed_generation_id=OLD.id)) THEN
        -- Only terminal exact work may be adopted into a new ordinary window.
        -- The normal demand guard then freezes this exact key and live fence.
        RETURN NEW;
    ELSIF demand.claimed_generation_id IS DISTINCT FROM NEW.id
        OR NOT EXISTS(SELECT 1 FROM stewardship_task_run WHERE id=OLD.task_id AND root_id=owned_task.root_id) THEN
        RAISE EXCEPTION 'Fact mutation differs from its frozen root' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER fact_runtime_binding BEFORE INSERT OR UPDATE
    ON public.stewardship_daily_fact_set FOR EACH ROW
    EXECUTE FUNCTION public.stewardship_fact_runtime_binding_v1();

CREATE FUNCTION public.stewardship_fact_input_pin_commit_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF current_user='pk_stewardship_worker' AND NEW.parent_kind='facts'
        AND NOT EXISTS(SELECT 1 FROM stewardship_daily_fact_set f
            JOIN stewardship_task_run t ON t.id=f.task_id
            WHERE f.id=NEW.parent_id AND f.source_id=NEW.snapshot_id
                AND f.population_scope='current' AND f.state='building'
                AND t.task_type IN ('report_facts','report_exact_export')
                AND stewardship_fact_live(f.task_id,f.task_fence,f.worker_id)) THEN
        RAISE EXCEPTION 'Worker fact input pin requires its exact retained builder'
            USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER fact_input_pin_commit AFTER INSERT ON public.stewardship_source_pin
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
    EXECUTE FUNCTION public.stewardship_fact_input_pin_commit_v1();
