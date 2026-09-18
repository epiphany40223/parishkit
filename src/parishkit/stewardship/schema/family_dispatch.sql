-- Fresh-install Family dispatch ownership. Private key mounts alone never
-- authorize edits; each send/write also requires an exact live Task and scope.
CREATE FUNCTION public.stewardship_family_dispatch_live_v1(message uuid)
RETURNS boolean LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT public.stewardship_receipt_dispatch_live_v1(message) OR EXISTS (
        SELECT 1 FROM public.stewardship_outbox_message m
        JOIN public.stewardship_schedule_occurrence o ON o.id=m.semantic_key AND o.outbox_id=m.id
        JOIN public.stewardship_schedule_definition d ON d.id=o.definition_id
        JOIN public.stewardship_campaign c ON c.id=m.campaign_id
        JOIN public.stewardship_system_configuration r ON r.current_campaign_id=c.id
        JOIN public.stewardship_campaign_configuration p ON p.id=c.active_configuration_id
        JOIN public.stewardship_campaign_credentials k ON k.campaign_id=c.id
        JOIN public.stewardship_family_campaign f ON f.id=m.family_id AND f.campaign_id=c.id
        JOIN public.stewardship_source_current source ON source.singleton
        WHERE m.id=message AND d.kind=m.purpose AND d.campaign_id=c.id
          AND m.purpose IN ('initial','reminder') AND o.target='family:'||f.id::text
          AND o.mode=m.mode AND o.routing=m.routing AND r.mode=m.mode
          AND o.revision_id=d.current_revision_id AND o.state IN ('pending','running')
          AND o.due_at<=public.stewardship_campaign_now_v1()
          AND public.stewardship_campaign_now_v1()>=p.starts_at
          AND public.stewardship_campaign_now_v1()<p.ends_at
          AND NOT r.restore_review_required AND NOT k.go_live_gate AND NOT k.population_dirty
          AND k.source_snapshot_id=source.snapshot_id AND k.source_generation=source.generation
          AND f.source_generation=source.generation AND f.active AND f.email_eligible AND f.email_deliverable
          AND NOT EXISTS (SELECT 1 FROM public.stewardship_campaign_work_gate gate
              WHERE gate.campaign_id=c.id AND gate.state<>'released')
          AND NOT EXISTS (SELECT 1 FROM public.stewardship_restore_delivery_hold hold
              WHERE hold.definition_id=d.id AND hold.mode=m.mode AND hold.target=o.target
                AND hold.slot=o.slot AND hold.state IN ('unreviewed','assumed_delivered'))
          AND NOT EXISTS (SELECT 1 FROM public.stewardship_schedule_fulfillment covered
              WHERE covered.definition_id=d.id AND covered.mode=m.mode
                AND covered.target=o.target AND covered.slot=o.slot)
          AND NOT EXISTS (SELECT 1 FROM public.stewardship_outbox_message unresolved
              WHERE unresolved.family_id=f.id AND unresolved.campaign_id=c.id AND unresolved.mode=m.mode
                AND unresolved.id<>m.id AND unresolved.state IN ('submitting','delivery_unknown'))
          AND (m.purpose<>'reminder' OR NOT EXISTS (
              SELECT 1 FROM public.stewardship_restore_delivery_hold initial_hold
              JOIN public.stewardship_schedule_definition initial ON initial.id=initial_hold.definition_id
              WHERE initial.campaign_id=c.id AND initial.kind='initial' AND initial_hold.mode=m.mode
                AND initial_hold.target=o.target AND initial_hold.slot='once' AND initial_hold.state='unreviewed'))
          AND ((m.mode='testing' AND c.state='draft' AND m.credential_namespace='rehearsal'
                AND m.rehearsal_epoch_id=k.rehearsal_epoch_id
                AND EXISTS (SELECT 1 FROM public.stewardship_rehearsal_epoch e
                    WHERE e.id=m.rehearsal_epoch_id AND e.state='active')
                AND NOT EXISTS (SELECT 1 FROM public.stewardship_submission sub
                    WHERE sub.family_id=f.id AND sub.mode='test' AND sub.rehearsal_epoch_id=m.rehearsal_epoch_id))
            OR (m.mode='production' AND c.state IN ('scheduled','active') AND NOT c.delivery_paused
                AND f.effective_submission_id IS NULL AND m.credential_namespace='production'
                AND m.token_generation_id=c.active_token_generation_id
                AND EXISTS (SELECT 1 FROM public.stewardship_family_token_generation generation
                    JOIN public.stewardship_credential_deployment deployment
                      ON deployment.family_link_epoch=generation.credential_epoch
                    WHERE generation.id=m.token_generation_id AND generation.state='active'
                      AND generation.credential_epoch=m.credential_epoch_id)
                AND NOT EXISTS (SELECT 1 FROM public.stewardship_activation_catchup demand
                    WHERE demand.campaign_id=c.id AND demand.completed_at IS NULL)))
    )
$$;

CREATE FUNCTION public.stewardship_family_dispatch_render_v1(proposed jsonb,message uuid)
RETURNS boolean LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT CASE m.purpose WHEN 'receipt' THEN
        public.stewardship_receipt_render_admitted_v1(proposed,m.family_id,m.campaign_id,r.active_configuration_id,m.mode)
    ELSE public.stewardship_family_mail_render_admitted_v1(proposed,m.family_id,r.active_configuration_id,
        (SELECT revision_id FROM public.stewardship_schedule_occurrence WHERE id=m.semantic_key),m.mode) END
    FROM public.stewardship_outbox_message m CROSS JOIN public.stewardship_system_configuration r
    WHERE m.id=message
$$;

CREATE FUNCTION public.stewardship_family_dispatch_write_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE t public.stewardship_task_run%ROWTYPE;
    m public.stewardship_outbox_message%ROWTYPE;
    own public.stewardship_outbox_message%ROWTYPE;
    proposed jsonb; rendering jsonb; recovering boolean;
BEGIN
    IF current_user<>'pk_stewardship_mail_dispatch' THEN RETURN NEW; END IF;
    IF NOT EXISTS(SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
        AND mode='ExclusiveLock' AND granted) THEN
        RAISE EXCEPTION 'Family dispatch requires work ownership' USING ERRCODE='23514'; END IF;
    proposed:=to_jsonb(NEW);
    SELECT * INTO t FROM public.stewardship_task_run WHERE id=NEW.correlation_id;
    SELECT * INTO own FROM public.stewardship_outbox_message
        WHERE id=t.domain_request_id AND task_id=t.root_id AND purpose IN ('initial','reminder','receipt','daily_digest','weekly_digest','operational');
    IF own.purpose='operational' THEN
        IF public.stewardship_ops_dispatch_write_v1(TG_TABLE_NAME,proposed,
            CASE WHEN TG_OP='UPDATE' THEN to_jsonb(OLD) ELSE NULL END) IS NOT TRUE THEN
            RAISE EXCEPTION 'Operational dispatch requires its exact current owner' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    IF own.purpose='weekly_digest' THEN
        IF public.stewardship_weekly_dispatch_write_v1(TG_TABLE_NAME,proposed,
            CASE WHEN TG_OP='UPDATE' THEN to_jsonb(OLD) ELSE NULL END) IS NOT TRUE THEN
            RAISE EXCEPTION 'Weekly dispatch requires its exact current owner' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    IF own.purpose='daily_digest' THEN
        IF public.stewardship_daily_dispatch_write_v1(TG_TABLE_NAME,proposed,
            CASE WHEN TG_OP='UPDATE' THEN to_jsonb(OLD) ELSE NULL END) IS NOT TRUE THEN
            RAISE EXCEPTION 'Daily dispatch requires its exact current owner' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    recovering:=t.state='abandoned' AND own.provider_deadline<=clock_timestamp()
        AND own.run_id=t.id AND own.task_fence<=t.fence AND NEW.actor_id IS NOT NULL
        AND proposed->>'reason'='recovery_unknown'
        AND ((TG_TABLE_NAME='stewardship_outbox_message' AND proposed->>'id'=own.id::text
                AND proposed->>'action'='mark_unknown' AND own.state='submitting')
            OR (TG_TABLE_NAME='stewardship_outbox_event' AND proposed->>'message_id'=own.id::text
                AND proposed->>'action'='mark_unknown' AND own.state='delivery_unknown')
            OR (TG_TABLE_NAME='stewardship_schedule_occurrence' AND proposed->>'id'=own.semantic_key::text
                AND proposed->>'state'='delivery_unknown' AND own.state='delivery_unknown'));
    IF recovering THEN RETURN NEW; END IF;
    IF t.id IS NULL OR t.task_type<>'outbox_delivery' OR t.state<>'running'
       OR t.worker_id IS DISTINCT FROM NEW.actor_id OR t.lease_expires_at<=clock_timestamp()
       THEN RAISE EXCEPTION 'Family dispatch requires a live exact claim' USING ERRCODE='23514'; END IF;
    IF own.id IS NULL THEN RAISE EXCEPTION 'Family dispatch root differs' USING ERRCODE='23514'; END IF;
    IF TG_TABLE_NAME='stewardship_outbox_message' THEN
        IF TG_OP<>'UPDATE' THEN RAISE EXCEPTION 'Mail cannot allocate Family messages' USING ERRCODE='23514'; END IF;
        m:=NEW;
        IF m.id<>own.id AND (m.action<>'cancel_unsent' OR m.family_id<>own.family_id
            OR m.purpose='receipt' OR own.purpose='receipt'
            OR m.campaign_id<>own.campaign_id OR m.mode<>own.mode) THEN
            RAISE EXCEPTION 'Family dispatch cannot mutate another delivery' USING ERRCODE='23514'; END IF;
        IF m.action NOT IN ('prepared','submit','accept','retry_unaccepted','fail_unaccepted','mark_unknown',
            'cancel_unsent','hold','release_hold') THEN
            RAISE EXCEPTION 'Family dispatch action requires another owner' USING ERRCODE='23514'; END IF;
        IF m.action IN ('prepared','submit') THEN
            IF public.stewardship_family_dispatch_live_v1(m.id) IS NOT TRUE THEN
                RAISE EXCEPTION 'Family dispatch live scope is unavailable' USING ERRCODE='23514'; END IF;
            SELECT to_jsonb(r) INTO rendering FROM public.stewardship_outbox_render r WHERE id=m.render_id;
            IF public.stewardship_family_dispatch_render_v1(rendering,m.id) IS NOT TRUE THEN
                RAISE EXCEPTION 'Family dispatch render differs from current scope' USING ERRCODE='23514'; END IF;
        ELSIF m.action IN ('accept','retry_unaccepted','fail_unaccepted','mark_unknown') THEN
            IF OLD.state<>'submitting' OR OLD.run_id<>t.id OR OLD.task_fence<>t.fence
               OR OLD.worker_id IS DISTINCT FROM t.worker_id OR m.reason NOT LIKE 'smtp_%' THEN
                RAISE EXCEPTION 'Family outcome claim differs' USING ERRCODE='23514'; END IF;
        END IF;
    ELSIF TG_TABLE_NAME='stewardship_outbox_render' THEN
        IF NEW.message_id<>own.id OR own.state NOT IN ('pending','retry_wait')
           OR public.stewardship_family_dispatch_live_v1(own.id) IS NOT TRUE
           OR public.stewardship_family_dispatch_render_v1(proposed,own.id) IS NOT TRUE THEN
            RAISE EXCEPTION 'Family dispatch rendering is not admitted' USING ERRCODE='23514'; END IF;
    ELSIF TG_TABLE_NAME='stewardship_outbox_event' THEN
        IF NEW.message_id<>own.id AND NOT EXISTS (
            SELECT 1 FROM public.stewardship_outbox_message sibling
            WHERE sibling.id=NEW.message_id AND sibling.family_id=own.family_id
              AND sibling.campaign_id=own.campaign_id AND sibling.mode=own.mode
              AND sibling.purpose IN ('initial','reminder') AND own.purpose IN ('initial','reminder')
              AND sibling.state='cancelled' AND NEW.action='cancel_unsent'
        ) THEN RAISE EXCEPTION 'Family event scope differs' USING ERRCODE='23514'; END IF;
    ELSIF TG_TABLE_NAME='stewardship_schedule_occurrence' THEN
        IF own.purpose='receipt' OR NEW.target<>'family:'||own.family_id::text OR NEW.mode<>own.mode OR NOT EXISTS (
            SELECT 1 FROM public.stewardship_schedule_definition d
            WHERE d.id=NEW.definition_id AND d.campaign_id=own.campaign_id AND d.kind IN ('initial','reminder')
        ) THEN RAISE EXCEPTION 'Family occurrence scope differs' USING ERRCODE='23514'; END IF;
    ELSIF TG_TABLE_NAME='stewardship_schedule_fulfillment' THEN
        IF own.purpose='receipt' OR NEW.target<>'family:'||own.family_id::text OR NEW.mode<>own.mode OR NOT EXISTS (
            SELECT 1 FROM public.stewardship_schedule_definition d
            WHERE d.id=NEW.definition_id AND d.campaign_id=own.campaign_id AND d.kind IN ('initial','reminder')
        ) THEN RAISE EXCEPTION 'Family fulfillment scope differs' USING ERRCODE='23514'; END IF;
    ELSE RAISE EXCEPTION 'Unknown Family dispatch write' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;

CREATE FUNCTION public.stewardship_family_dispatch_result_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF current_user='pk_stewardship_mail_dispatch' AND NEW.reason LIKE 'smtp_%'
       AND public.stewardship_family_smtp_result_v1(NEW.id) IS NULL THEN
        RAISE EXCEPTION 'Family provider result is invalid' USING ERRCODE='23514'; END IF;
    RETURN NULL;
END $$;

CREATE TRIGGER family_dispatch_outbox BEFORE INSERT OR UPDATE ON public.stewardship_outbox_message
FOR EACH ROW EXECUTE FUNCTION public.stewardship_family_dispatch_write_v1();
CREATE TRIGGER family_dispatch_render BEFORE INSERT ON public.stewardship_outbox_render
FOR EACH ROW EXECUTE FUNCTION public.stewardship_family_dispatch_write_v1();
CREATE TRIGGER family_dispatch_event BEFORE INSERT ON public.stewardship_outbox_event
FOR EACH ROW EXECUTE FUNCTION public.stewardship_family_dispatch_write_v1();
CREATE TRIGGER family_dispatch_result AFTER INSERT ON public.stewardship_outbox_event
FOR EACH ROW EXECUTE FUNCTION public.stewardship_family_dispatch_result_v1();
CREATE TRIGGER family_dispatch_occurrence BEFORE INSERT OR UPDATE ON public.stewardship_schedule_occurrence
FOR EACH ROW EXECUTE FUNCTION public.stewardship_family_dispatch_write_v1();
CREATE TRIGGER family_dispatch_fulfillment BEFORE INSERT ON public.stewardship_schedule_fulfillment
FOR EACH ROW EXECUTE FUNCTION public.stewardship_family_dispatch_write_v1();
REVOKE ALL ON FUNCTION public.stewardship_family_dispatch_write_v1() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.stewardship_family_dispatch_result_v1() FROM PUBLIC;
