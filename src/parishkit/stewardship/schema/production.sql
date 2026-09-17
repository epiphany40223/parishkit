-- Go-live journal foundation. Final activation remains deliberately unavailable
-- until the compiled readiness/activation workflow owns every required effect.

CREATE FUNCTION public.stewardship_production_task_actor_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog, public, pg_temp AS $$
BEGIN
    -- The domain journal must be able to mirror a terminal recovery outcome.
    -- Reject missing attribution at its source, before the task can be stranded.
    IF NEW.task_type='production_cleanup' AND NEW.action='recovery_fail'
       AND NEW.actor_id IS NULL THEN
        RAISE EXCEPTION 'Production task recovery requires an attributed actor'
            USING ERRCODE='23514';
    END IF;
    -- A crash after the domain committed completion may finish its task. An
    -- unfinished request instead needs recovery_retry or attributed failure;
    -- succeeding its task first would remove every resumable domain edge.
    IF NEW.task_type='production_cleanup' AND NEW.action='recovery_complete'
       AND NOT EXISTS (
           SELECT 1 FROM public.stewardship_production_request
           WHERE id=NEW.domain_request_id AND task_id=NEW.root_id
             AND run_id=NEW.id AND state='cleanup_complete'
       ) THEN
        RAISE EXCEPTION 'Production task recovery requires committed cleanup completion'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER stewardship_production_task_actor
    BEFORE UPDATE ON public.stewardship_task_run
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_production_task_actor_v1();
REVOKE ALL ON FUNCTION public.stewardship_production_task_actor_v1() FROM PUBLIC;

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
    IF (NEW.failure_reason<>'') IS DISTINCT FROM (NEW.action IN ('fail','retry_later','recovery_fail')) THEN
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
            ('cleanup_queued','recovery_fail','cleanup_failed'),
            ('cleanup_running','recovery_fail','cleanup_failed'),
            ('cleanup_retry_wait','recovery_fail','cleanup_failed'),
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
    IF NEW.action IN ('start','recover','recovery_fail') THEN
        allowed := allowed || ARRAY['run_id','task_fence','worker_id'];
    END IF;
    IF NEW.action='recovery_fail' THEN
        SELECT * INTO claim FROM public.stewardship_task_run
            WHERE root_id=NEW.task_id ORDER BY retry_sequence DESC LIMIT 1;
        -- Recovery first fences the dead worker and records a terminal TaskRun
        -- outcome. This edge mirrors that exact result, not an unclaimed guess.
        IF claim.id IS DISTINCT FROM NEW.run_id OR claim.state<>'failed'
           OR claim.action<>'recovery_fail' OR claim.fence IS DISTINCT FROM NEW.task_fence
           OR claim.worker_id IS DISTINCT FROM NEW.worker_id
           OR NEW.actor_id IS DISTINCT FROM claim.actor_id OR NEW.actor_id IS NULL
           OR (OLD.run_id IS NOT NULL AND NOT (
               (NEW.run_id=OLD.run_id AND NEW.task_fence>OLD.task_fence)
               OR (NEW.run_id<>OLD.run_id AND claim.retry_sequence>(
                   SELECT retry_sequence FROM public.stewardship_task_run WHERE id=OLD.run_id
               ))
           )) THEN
            RAISE EXCEPTION 'Cleanup recovery must match its fenced failed task' USING ERRCODE='23514';
        END IF;
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

-- Closed, independently verified inventory. This function has invoker rights;
-- it neither grants private SELECT access nor authorizes any deletion.
-- All relations are qualified. Deliberately omit a function-level SET clause:
-- SQL inlining must push exact category/UUID predicates into the indexed source
-- branches instead of rebuilding the whole campaign inventory for each target.
CREATE FUNCTION public.stewardship_cleanup_inventory_v1(campaign_uuid uuid)
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
$$;
REVOKE ALL ON FUNCTION public.stewardship_cleanup_inventory_v1(uuid) FROM PUBLIC;

CREATE FUNCTION public.stewardship_cleanup_selection_open_v1(request_uuid uuid)
RETURNS boolean LANGUAGE sql STABLE SET search_path TO pg_catalog, public, pg_temp AS $$
    SELECT EXISTS (
        SELECT 1 FROM public.stewardship_production_request r
        JOIN public.stewardship_system_configuration s ON s.current_campaign_id=r.campaign_id
        JOIN public.stewardship_campaign_credentials c ON c.campaign_id=r.campaign_id
        JOIN public.stewardship_campaign campaign ON campaign.id=r.campaign_id AND campaign.state='draft'
        WHERE r.id=request_uuid AND r.state='cleanup_queued' AND r.version=1
          AND s.mode='testing' AND NOT s.restore_review_required
          AND s.active_configuration_id=r.configuration_id
          AND c.go_live_gate AND c.rehearsal_epoch_id IS NULL AND c.version=r.gate_version
          AND NOT EXISTS (SELECT 1 FROM public.stewardship_campaign_work_gate WHERE state IN ('preparing','running'))
    ) AND EXISTS (
        SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid() AND locktype='advisory'
          AND classid=736220 AND objid=1 AND objsubid=2 AND mode='ExclusiveLock' AND granted
    )
$$;
REVOKE ALL ON FUNCTION public.stewardship_cleanup_selection_open_v1(uuid) FROM PUBLIC;

CREATE FUNCTION public.stewardship_production_target_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog, public, pg_temp AS $$
BEGIN
    IF TG_OP='UPDATE' THEN
        RAISE EXCEPTION 'Cleanup membership requires its bounded deletion owner' USING ERRCODE='23514';
    END IF;
    IF TG_OP='DELETE' THEN
        IF public.stewardship_cleanup_effect_v1('prior_inventory_targets',OLD.id)
           AND EXISTS (SELECT 1 FROM public.stewardship_production_request
               WHERE id=OLD.request_id AND state='cancelled') THEN
            RETURN OLD;
        END IF;
        IF public.stewardship_cleanup_effect_v1(OLD.category,OLD.target_id)
           AND NOT public.stewardship_cleanup_target_exists_v1(OLD.category,OLD.target_id) THEN
            RETURN OLD;
        END IF;
        RAISE EXCEPTION 'Cleanup membership requires its bounded deletion owner' USING ERRCODE='23514';
    END IF;
    IF NOT public.stewardship_cleanup_selection_open_v1(NEW.request_id)
       OR EXISTS (SELECT 1 FROM public.stewardship_production_manifest WHERE request_id=NEW.request_id)
       OR NOT EXISTS (
           SELECT 1 FROM public.stewardship_production_request WHERE id=NEW.request_id
             AND initiated_by_id=NEW.actor_id AND correlation_id=NEW.correlation_id
       ) THEN
        RAISE EXCEPTION 'Cleanup selection is not open for this request' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;

CREATE FUNCTION public.stewardship_production_manifest_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog, public, pg_temp AS $$
DECLARE
    request public.stewardship_production_request%ROWTYPE;
    counts jsonb;
    fingerprint text;
    deliveries jsonb;
    attempts bigint;
    templates jsonb;
    recipient_fingerprint text;
BEGIN
    IF TG_OP<>'INSERT' THEN
        RAISE EXCEPTION 'Historical records are append-only' USING ERRCODE='23514';
    END IF;
    SELECT * INTO request FROM public.stewardship_production_request WHERE id=NEW.request_id;
    IF NOT public.stewardship_cleanup_selection_open_v1(NEW.request_id)
       OR request.initiated_by_id IS DISTINCT FROM NEW.actor_id
       OR request.correlation_id IS DISTINCT FROM NEW.correlation_id THEN
        RAISE EXCEPTION 'Cleanup manifest requires its current gated request' USING ERRCODE='23514';
    END IF;
    -- Both EXCEPT directions matter: matching totals cannot conceal omission or
    -- the substitution of live, operational, other-campaign or nonexistent IDs.
    IF EXISTS (
        (SELECT category,target_id FROM public.stewardship_cleanup_inventory_v1(request.campaign_id)
         EXCEPT SELECT category,target_id FROM public.stewardship_production_target WHERE request_id=NEW.request_id)
        UNION ALL
        (SELECT category,target_id FROM public.stewardship_production_target WHERE request_id=NEW.request_id
         EXCEPT SELECT category,target_id FROM public.stewardship_cleanup_inventory_v1(request.campaign_id))
    ) THEN
        RAISE EXCEPTION 'Cleanup manifest differs from the exact Testing corpus' USING ERRCODE='23514';
    END IF;
    -- External workflow history is not part of this deletion owner. Refuse an
    -- impossible manifest before invalidation/gate capture can commit, rather
    -- than retrying a permanently blocked dependency after partial deletion.
    IF EXISTS (
        SELECT 1 FROM public.stewardship_production_target i
        WHERE i.request_id=NEW.request_id AND i.category='occurrences' AND (
            EXISTS (SELECT 1 FROM public.stewardship_restore_delivery_hold WHERE recovery_occurrence_id=i.target_id)
            OR EXISTS (SELECT 1 FROM public.stewardship_restore_hold_resolution WHERE recovery_occurrence_id=i.target_id)
            OR EXISTS (SELECT 1 FROM public.stewardship_postclose_resolution WHERE occurrence_id=i.target_id)
            OR EXISTS (SELECT 1 FROM public.stewardship_schedule_occurrence o
                JOIN public.stewardship_outbox_message m ON m.id=o.outbox_id
                WHERE o.id=i.target_id AND NOT EXISTS (
                    SELECT 1 FROM public.stewardship_production_target other
                    WHERE other.request_id=NEW.request_id AND other.category='outbox_messages' AND other.target_id=m.id))
            OR EXISTS (SELECT 1 FROM public.stewardship_schedule_occurrence o
                WHERE o.replacement_id=i.target_id AND NOT EXISTS (
                    SELECT 1 FROM public.stewardship_production_target other
                    WHERE other.request_id=NEW.request_id AND other.category='occurrences' AND other.target_id=o.id))
        )
    ) THEN
        RAISE EXCEPTION 'Cleanup inventory retains external workflow references' USING ERRCODE='23514';
    END IF;
    -- UUID-only outbox references must also be checked in reverse. Retained
    -- workflow history must not become dangling even if its original binding
    -- was malformed; reject capture instead of deleting or repairing history.
    IF EXISTS (
        SELECT 1 FROM public.stewardship_schedule_occurrence o
        JOIN public.stewardship_production_target message
            ON message.request_id=NEW.request_id AND message.category='outbox_messages'
            AND message.target_id=o.outbox_id
        WHERE NOT EXISTS (SELECT 1 FROM public.stewardship_production_target occurrence
            WHERE occurrence.request_id=NEW.request_id AND occurrence.category='occurrences'
                AND occurrence.target_id=o.id)
        UNION ALL
        SELECT 1 FROM public.stewardship_postclose_resolution p
        JOIN public.stewardship_production_target message
            ON message.request_id=NEW.request_id AND message.category='outbox_messages'
            AND message.target_id=p.outbox_id
    ) THEN
        RAISE EXCEPTION 'Cleanup inventory retains external outbox references' USING ERRCODE='23514';
    END IF;
    -- Other dependent ownership is already immutable: fulfillment_guard_v1
    -- requires the occurrence's mode and campaign; baseline/submission guards
    -- bind prior_submission to the same Family, mode and rehearsal epoch.
    SELECT COALESCE(jsonb_object_agg(category,total),'{}'::jsonb) INTO counts FROM (
        SELECT category,count(*) total FROM public.stewardship_production_target
        WHERE request_id=NEW.request_id GROUP BY category
    ) grouped;
    SELECT encode(sha256(
        convert_to('stewardship-production-cleanup-inventory-v1','UTF8') || decode('00','hex') ||
        COALESCE(string_agg(convert_to(category,'UTF8') || decode('00','hex') || uuid_send(target_id),
            ''::bytea ORDER BY category COLLATE "C",target_id),''::bytea)
    ),'hex') INTO fingerprint FROM public.stewardship_production_target WHERE request_id=NEW.request_id;
    IF counts IS DISTINCT FROM request.inventory_counts OR fingerprint IS DISTINCT FROM request.inventory_digest THEN
        RAISE EXCEPTION 'Cleanup manifest does not match acknowledged evidence' USING ERRCODE='23514';
    END IF;
    IF EXISTS (SELECT 1 FROM (
        SELECT position,row_number() OVER (ORDER BY category COLLATE "C",target_id) expected
        FROM public.stewardship_production_target WHERE request_id=NEW.request_id
    ) ordered WHERE position<>expected) THEN
        RAISE EXCEPTION 'Cleanup scan positions must match canonical inventory order' USING ERRCODE='23514';
    END IF;
    IF (SELECT ROW(count(*),count(DISTINCT s.family_id))
        FROM public.stewardship_submission s
        JOIN public.stewardship_production_target i ON i.target_id=s.id
        WHERE i.request_id=NEW.request_id AND i.category='submissions')
       IS DISTINCT FROM (SELECT ROW(submissions,families)
           FROM public.stewardship_testing_aggregate WHERE id=request.aggregate_id) THEN
        RAISE EXCEPTION 'Cleanup requires exact Testing response totals' USING ERRCODE='23514';
    END IF;
    SELECT COALESCE(jsonb_object_agg(purpose,results),'{}'::jsonb) INTO deliveries FROM (
        SELECT purpose,jsonb_object_agg(state,total) results FROM (
            SELECT purpose,state,count(*) total FROM public.stewardship_outbox_message
            WHERE campaign_id=request.campaign_id AND mode='testing' AND routing='testing_override'
            GROUP BY purpose,state
        ) totals GROUP BY purpose
    ) grouped;
    SELECT COALESCE(sum(attempt),0) INTO attempts FROM public.stewardship_outbox_message
        WHERE campaign_id=request.campaign_id AND mode='testing' AND routing='testing_override';
    SELECT COALESCE(jsonb_agg(template_id ORDER BY template_id),'[]'::jsonb) INTO templates FROM (
        SELECT DISTINCT r.template_id FROM public.stewardship_outbox_render r
        JOIN public.stewardship_outbox_message m ON m.id=r.message_id
        WHERE m.campaign_id=request.campaign_id AND m.mode='testing' AND m.routing='testing_override'
          AND r.template_id IS NOT NULL
    ) versions;
    SELECT encode(sha256(convert_to('stewardship-testing-recipient-v1','UTF8') || decode('00','hex') ||
        convert_to(testing_recipient,'UTF8')),'hex') INTO recipient_fingerprint
        FROM public.stewardship_system_configuration;
    IF EXISTS (
        SELECT 1 FROM public.stewardship_outbox_message WHERE campaign_id=request.campaign_id
          AND routing='testing_override' AND state NOT IN ('delivered','permanent_failure','cancelled')
    ) OR NEW.delivery_counts IS DISTINCT FROM deliveries OR NEW.delivery_attempts IS DISTINCT FROM attempts
      OR NEW.template_ids IS DISTINCT FROM templates
      OR NEW.testing_recipient_fingerprint IS DISTINCT FROM recipient_fingerprint THEN
        RAISE EXCEPTION 'Cleanup requires exact non-sensitive Testing delivery evidence' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;

CREATE FUNCTION public.stewardship_production_target_pin_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog, public, pg_temp AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM public.stewardship_production_manifest WHERE request_id=NEW.request_id) THEN
        RAISE EXCEPTION 'Cleanup targets must commit with a sealed manifest' USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END $$;

ALTER TABLE public.stewardship_production_manifest ADD CONSTRAINT production_manifest_request_fk
    FOREIGN KEY (request_id) REFERENCES public.stewardship_production_request(id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE public.stewardship_production_target ADD CONSTRAINT production_target_request_fk
    FOREIGN KEY (request_id) REFERENCES public.stewardship_production_request(id) DEFERRABLE INITIALLY DEFERRED;
CREATE TRIGGER stewardship_production_manifest_guard
    BEFORE INSERT ON public.stewardship_production_manifest
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_production_manifest_guard_v1();
CREATE TRIGGER stewardship_production_target_guard
    BEFORE INSERT OR UPDATE OR DELETE ON public.stewardship_production_target
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_production_target_guard_v1();
CREATE CONSTRAINT TRIGGER stewardship_production_target_pin
    AFTER INSERT ON public.stewardship_production_target DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_production_target_pin_v1();
REVOKE ALL ON FUNCTION public.stewardship_production_manifest_guard_v1() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.stewardship_production_target_guard_v1() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.stewardship_production_target_pin_v1() FROM PUBLIC;

CREATE FUNCTION public.stewardship_production_manifest_immutable_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog, public, pg_temp AS $$
BEGIN
    RAISE EXCEPTION 'Historical records are append-only' USING ERRCODE = '23514';
END $$;
CREATE TRIGGER stewardship_production_manifest_immutable_guard_v1
    BEFORE UPDATE OR DELETE ON public.stewardship_production_manifest
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_production_manifest_immutable_v1();
REVOKE ALL ON FUNCTION public.stewardship_production_manifest_immutable_v1() FROM PUBLIC;
