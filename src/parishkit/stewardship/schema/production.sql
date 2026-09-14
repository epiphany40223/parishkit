-- Go-live journal foundation. Final activation remains deliberately unavailable
-- until the compiled readiness/activation workflow owns every required effect.

CREATE FUNCTION public.stewardship_cleanup_counts_v1(counts jsonb) RETURNS bigint
LANGUAGE plpgsql IMMUTABLE SET search_path TO pg_catalog, public, pg_temp AS $$
DECLARE
    entry record;
    total numeric := 0;
BEGIN
    IF jsonb_typeof(counts) IS DISTINCT FROM 'object' THEN
        RAISE EXCEPTION 'Invalid cleanup counts' USING ERRCODE='23514';
    END IF;
    FOR entry IN SELECT * FROM jsonb_each(counts) LOOP
        IF entry.key !~ '^[a-z][a-z0-9_]{0,63}$'
           OR jsonb_typeof(entry.value)<>'number'
           OR entry.value::text !~ '^(0|[1-9][0-9]*)$' THEN
            RAISE EXCEPTION 'Invalid cleanup counts' USING ERRCODE='23514';
        END IF;
        total := total + entry.value::text::numeric;
    END LOOP;
    IF total > 9223372036854775807 THEN
        RAISE EXCEPTION 'Cleanup counts exceed storage bounds' USING ERRCODE='23514';
    END IF;
    RETURN total::bigint;
END $$;

CREATE FUNCTION public.stewardship_production_state_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog, public, pg_temp AS $$
DECLARE
    claim public.stewardship_task_run%ROWTYPE;
    batch public.stewardship_production_checkpoint%ROWTYPE;
    totals public.stewardship_testing_aggregate%ROWTYPE;
    allowed text[];
BEGIN
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'Production requests require their retention owner' USING ERRCODE='23514';
    END IF;
    IF NEW.action='activate' OR NEW.state='activated' THEN
        RAISE EXCEPTION 'Production activation requires its later owning workflow'
            USING ERRCODE='23514';
    END IF;
    IF (NEW.failure_reason<>'') IS DISTINCT FROM (NEW.action IN ('fail','retry_later')) THEN
        RAISE EXCEPTION 'Invalid cleanup failure reason for this action' USING ERRCODE='23514';
    END IF;
    IF NEW.inventory_total <> public.stewardship_cleanup_counts_v1(NEW.inventory_counts)
       OR NEW.reauthenticated_at>NEW.acknowledged_at
       OR NEW.acknowledged_at>statement_timestamp() OR NOT EXISTS (
           SELECT 1 FROM public.stewardship_testing_aggregate
           WHERE id=NEW.aggregate_id AND campaign_id=NEW.campaign_id
             AND inventory_digest=NEW.inventory_digest
       ) OR NOT EXISTS (
           SELECT 1 FROM public.stewardship_task_run
           WHERE id=NEW.task_id AND root_id=id AND task_type='production_cleanup'
             AND domain_request_id=NEW.id
       ) THEN
        RAISE EXCEPTION 'Invalid Production request binding' USING ERRCODE='23514';
    END IF;
    IF TG_OP='INSERT' THEN
        IF NEW.state<>'cleanup_queued' OR NEW.action<>'created' OR NEW.version<>1
           OR NEW.processed_count<>0 OR NEW.checkpoint_sequence<>0 OR NEW.run_id IS NOT NULL
           OR NEW.actor_id IS DISTINCT FROM NEW.initiated_by_id THEN
            RAISE EXCEPTION 'Invalid initial Production request' USING ERRCODE='23514';
        END IF;
        SELECT * INTO totals FROM public.stewardship_testing_aggregate WHERE id=NEW.aggregate_id;
        IF EXISTS (
            SELECT 1 FROM public.stewardship_outbox_message
            WHERE campaign_id=NEW.campaign_id AND routing='testing_override'
              AND state NOT IN ('delivered','permanent_failure','cancelled')
        ) OR (SELECT ROW(count(*),count(*) FILTER (WHERE state='delivered'),
                        count(*) FILTER (WHERE state='permanent_failure'),
                        count(*) FILTER (WHERE state='cancelled'))
              FROM public.stewardship_outbox_message
              WHERE campaign_id=NEW.campaign_id AND routing='testing_override')
              IS DISTINCT FROM ROW(totals.messages,totals.delivered,totals.failed,totals.cancelled) THEN
            RAISE EXCEPTION 'Production cleanup requires exact terminal Testing delivery totals'
                USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.command_id=OLD.command_id OR NOT EXISTS (
        SELECT 1 FROM (VALUES
            ('cleanup_queued','start','cleanup_running'),
            ('cleanup_retry_wait','start','cleanup_running'),
            ('cleanup_running','recover','cleanup_running'),
            ('cleanup_running','checkpoint','cleanup_running'),
            ('cleanup_running','retry_later','cleanup_retry_wait'),
            ('cleanup_running','fail','cleanup_failed'),
            ('cleanup_failed','retry_failed','cleanup_queued'),
            ('cleanup_running','complete','cleanup_complete'),
            ('cleanup_queued','cancel','cancelled'),
            ('cleanup_running','cancel','cancelled'),
            ('cleanup_retry_wait','cancel','cancelled'),
            ('cleanup_failed','cancel','cancelled'),
            ('cleanup_complete','cancel','cancelled')
        ) edge(previous,action,target)
        WHERE edge.previous=OLD.state AND edge.action=NEW.action AND edge.target=NEW.state
    ) THEN
        RAISE EXCEPTION 'Invalid Production request transition' USING ERRCODE='23514';
    END IF;
    allowed := ARRAY['version','updated_at','actor_id','correlation_id','command_id',
                     'action','state','failure_reason'];
    IF NEW.action IN ('start','recover') THEN
        allowed := allowed || ARRAY['run_id','task_fence','worker_id'];
    END IF;
    IF NEW.action IN ('start','recover','checkpoint','retry_later','fail','complete') THEN
        SELECT * INTO claim FROM public.stewardship_task_run WHERE id=NEW.run_id;
        IF claim.id IS NULL OR claim.root_id<>NEW.task_id OR claim.state<>'running'
           OR claim.fence IS DISTINCT FROM NEW.task_fence
           OR claim.worker_id IS DISTINCT FROM NEW.worker_id
           OR NEW.actor_id IS DISTINCT FROM NEW.worker_id
           OR claim.lease_expires_at<=statement_timestamp() THEN
            RAISE EXCEPTION 'Production cleanup requires a current task claim' USING ERRCODE='23514';
        END IF;
    END IF;
    -- TaskRun recovery may commit before the domain journal can rebind. Only
    -- a later claim of this root may take over; never rewind checkpoint state.
    IF NEW.action='recover' AND NOT (
        (NEW.run_id=OLD.run_id AND NEW.task_fence>OLD.task_fence)
        OR (NEW.run_id<>OLD.run_id AND claim.retry_sequence>(
            SELECT retry_sequence FROM public.stewardship_task_run WHERE id=OLD.run_id
        ))
    ) THEN
        RAISE EXCEPTION 'Production recovery requires a newer task claim' USING ERRCODE='23514';
    END IF;
    IF NEW.action='checkpoint' THEN
        allowed := allowed || ARRAY['processed_count','checkpoint_sequence'];
        SELECT * INTO batch FROM public.stewardship_production_checkpoint
            WHERE request_id=NEW.id AND command_id=NEW.command_id;
        IF batch.id IS NULL OR batch.sequence<>OLD.checkpoint_sequence+1
           OR NEW.checkpoint_sequence<>batch.sequence
           OR NEW.processed_count<>OLD.processed_count+batch.deleted_count THEN
            RAISE EXCEPTION 'Cleanup progress requires its exact batch' USING ERRCODE='23514';
        END IF;
    END IF;
    IF NEW.action='complete' AND NEW.processed_count<>NEW.inventory_total THEN
        RAISE EXCEPTION 'Cleanup inventory is incomplete' USING ERRCODE='23514';
    END IF;
    IF NEW.action='retry_failed' AND NOT EXISTS (
        SELECT 1 FROM public.stewardship_task_run WHERE root_id=NEW.task_id
          AND parent_id=OLD.run_id AND retry_sequence>0 AND state='queued'
    ) THEN
        RAISE EXCEPTION 'Production cleanup needs its explicit task retry' USING ERRCODE='23514';
    END IF;
    IF NEW.action='cancel' AND EXISTS (
        SELECT 1 FROM public.stewardship_task_run
        WHERE root_id=NEW.task_id AND state IN ('running','abandoned')
    ) THEN
        RAISE EXCEPTION 'Production cancellation requires a safe task boundary' USING ERRCODE='23514';
    END IF;
    IF (to_jsonb(NEW)-allowed) IS DISTINCT FROM (to_jsonb(OLD)-allowed) THEN
        RAISE EXCEPTION 'Production command changed unrelated fields' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;

CREATE FUNCTION public.stewardship_production_gate_v1() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog, public, pg_temp AS $$
DECLARE
    campaign uuid;
BEGIN
    campaign := NEW.campaign_id;
    IF EXISTS (
        SELECT 1 FROM public.stewardship_production_request r
        WHERE r.campaign_id=campaign AND r.state NOT IN ('activated','cancelled')
          AND NOT EXISTS (
            SELECT 1 FROM public.stewardship_campaign_credentials c
            WHERE c.campaign_id=r.campaign_id AND c.go_live_gate
              AND c.rehearsal_epoch_id IS NULL AND c.version>=r.gate_version
          )
    ) THEN
        RAISE EXCEPTION 'Production request must retain its go-live gate' USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END $$;

CREATE FUNCTION public.stewardship_production_checkpoint_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog, public, pg_temp AS $$
DECLARE
    request public.stewardship_production_request%ROWTYPE;
    entry record;
    prior numeric;
BEGIN
    SELECT * INTO request FROM public.stewardship_production_request WHERE id=NEW.request_id;
    IF request.id IS NULL OR request.state<>'cleanup_running'
       OR NEW.sequence<>request.checkpoint_sequence+1
       OR NEW.deleted_count<>public.stewardship_cleanup_counts_v1(NEW.counts)
       OR NEW.run_id IS DISTINCT FROM request.run_id
       OR NEW.task_fence IS DISTINCT FROM request.task_fence
       OR NEW.worker_id IS DISTINCT FROM request.worker_id
       OR NEW.actor_id IS DISTINCT FROM request.worker_id THEN
        RAISE EXCEPTION 'Invalid Production cleanup checkpoint' USING ERRCODE='23514';
    END IF;
    FOR entry IN SELECT * FROM jsonb_each(NEW.counts) LOOP
        SELECT COALESCE(sum((counts->>entry.key)::numeric),0) INTO prior
            FROM public.stewardship_production_checkpoint WHERE request_id=NEW.request_id;
        IF NOT (request.inventory_counts ? entry.key)
           OR prior+entry.value::text::numeric>(request.inventory_counts->>entry.key)::numeric THEN
            RAISE EXCEPTION 'Cleanup batch exceeds its inventory' USING ERRCODE='23514';
        END IF;
    END LOOP;
    RETURN NEW;
END $$;

CREATE FUNCTION public.stewardship_production_history_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog, public, pg_temp AS $$
BEGIN
    INSERT INTO public.stewardship_production_event
        (id,created_at,actor_id,correlation_id,request_id,command_id,version,
         previous_state,state,action,snapshot)
    VALUES (gen_random_uuid(),NEW.updated_at,NEW.actor_id,NEW.correlation_id,NEW.id,
        NEW.command_id,NEW.version,CASE WHEN TG_OP='INSERT' THEN '' ELSE OLD.state END,
        NEW.state,NEW.action,to_jsonb(NEW));
    INSERT INTO public.stewardship_audit_event
        (id,actor_id,correlation_id,event_type,subject_id,campaign_reference)
    VALUES (gen_random_uuid(),NEW.actor_id,NEW.correlation_id,'production_'||NEW.action,NEW.id,NEW.campaign_id);
    RETURN NEW;
END $$;

CREATE FUNCTION public.stewardship_production_checkpoint_pin_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog, public, pg_temp AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM public.stewardship_production_event
        WHERE request_id=NEW.request_id AND command_id=NEW.command_id
          AND action='checkpoint'
          AND (snapshot->>'checkpoint_sequence')::bigint=NEW.sequence
          AND (snapshot->>'processed_count')::bigint>=NEW.deleted_count
    ) THEN
        RAISE EXCEPTION 'Cleanup checkpoint must commit with its progress event'
            USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END $$;

CREATE FUNCTION public.stewardship_production_event_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog, public, pg_temp AS $$
DECLARE
    request public.stewardship_production_request%ROWTYPE;
    previous public.stewardship_production_event%ROWTYPE;
BEGIN
    SELECT * INTO request FROM public.stewardship_production_request WHERE id=NEW.request_id;
    SELECT * INTO previous FROM public.stewardship_production_event
        WHERE request_id=NEW.request_id ORDER BY version DESC LIMIT 1;
    IF request.id IS NULL OR NEW.snapshot IS DISTINCT FROM to_jsonb(request)
       OR NEW.version IS DISTINCT FROM request.version
       OR NEW.version<>COALESCE(previous.version,0)+1
       OR NEW.previous_state IS DISTINCT FROM COALESCE(previous.state,'')
       OR NEW.state IS DISTINCT FROM request.state OR NEW.action IS DISTINCT FROM request.action
       OR NEW.command_id IS DISTINCT FROM request.command_id
       OR NEW.created_at IS DISTINCT FROM request.updated_at
       OR NEW.actor_id IS DISTINCT FROM request.actor_id
       OR NEW.correlation_id IS DISTINCT FROM request.correlation_id THEN
        RAISE EXCEPTION 'Invalid Production transition evidence' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER stewardship_production_state_guard
    BEFORE INSERT OR UPDATE OR DELETE ON public.stewardship_production_request
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_production_state_v1();
CREATE TRIGGER stewardship_production_history
    AFTER INSERT OR UPDATE ON public.stewardship_production_request
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_production_history_v1();
CREATE TRIGGER stewardship_production_event_binding
    BEFORE INSERT ON public.stewardship_production_event
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_production_event_v1();
CREATE TRIGGER stewardship_production_checkpoint_binding
    BEFORE INSERT ON public.stewardship_production_checkpoint
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_production_checkpoint_v1();
CREATE CONSTRAINT TRIGGER stewardship_production_checkpoint_pin
    AFTER INSERT ON public.stewardship_production_checkpoint DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_production_checkpoint_pin_v1();
CREATE CONSTRAINT TRIGGER stewardship_production_gate_pin
    AFTER INSERT OR UPDATE ON public.stewardship_production_request DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_production_gate_v1();
CREATE CONSTRAINT TRIGGER stewardship_production_credential_gate_pin
    AFTER UPDATE ON public.stewardship_campaign_credentials DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_production_gate_v1();

REVOKE ALL ON FUNCTION public.stewardship_cleanup_counts_v1(jsonb) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.stewardship_production_state_v1() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.stewardship_production_gate_v1() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.stewardship_production_checkpoint_v1() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.stewardship_production_history_v1() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.stewardship_production_event_v1() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.stewardship_production_checkpoint_pin_v1() FROM PUBLIC;
