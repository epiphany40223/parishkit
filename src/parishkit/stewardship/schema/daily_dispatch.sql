-- Daily messages share the isolated MAIL claim and SMTP evidence protocol, but
-- never inherit Family credential reads, refusal propagation or group writes.
CREATE FUNCTION stewardship_daily_dispatch_live_v1(message uuid)
RETURNS boolean LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT EXISTS(SELECT 1 FROM stewardship_outbox_message m
        JOIN stewardship_daily_digest_recipient recipient ON recipient.outbox_id=m.id AND recipient.id=m.semantic_key
        JOIN stewardship_daily_digest_ready ready ON ready.id=recipient.ready_id
        JOIN stewardship_daily_digest_snapshot s ON s.id=ready.snapshot_id
        JOIN stewardship_daily_digest_preparation p ON p.id=s.preparation_id
        JOIN stewardship_schedule_occurrence o ON o.id=p.occurrence_id
        JOIN stewardship_campaign c ON c.id=p.campaign_id
        JOIN stewardship_system_configuration runtime ON runtime.current_campaign_id=c.id
        JOIN stewardship_address_rule a ON a.configuration_id=runtime.active_configuration_id
            AND a.email=recipient.address AND a.roles @> '["administrator"]'::jsonb
        WHERE m.id=$1 AND m.purpose='daily_digest' AND m.credential_namespace='none' AND m.family_id IS NULL
          AND m.campaign_id=p.campaign_id AND m.mode=p.mode AND p.phase='complete'
          AND o.state IN ('pending','running') AND o.due_at<=stewardship_campaign_now_v1()
          AND NOT (m.mode='production' AND c.delivery_paused)
          AND stewardship_daily_digest_scope_v1(p.campaign_id,p.revision_id,p.campaign_configuration_id,p.mode,p.rehearsal_epoch_id)
          AND NOT stewardship_schedule_slot_excluded_v1(o.definition_id,o.mode,o.target,o.slot))
$$;

-- Web requests a retry but cannot copy private report content into an arbitrary
-- render. MAIL must replace this fixed, non-sendable seed under its live claim.
CREATE FUNCTION stewardship_daily_digest_seed_v1(message uuid,configuration uuid)
RETURNS jsonb LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE email jsonb; intended jsonb; routed jsonb; mode text; testing text;
    content jsonb; heading text:='Daily report awaiting preparation';
    body text:='PARISHKIT_PENDING_DAILY_DIGEST: Daily report awaiting preparation.';
BEGIN
    SELECT settings INTO email FROM stewardship_applied_integration
        WHERE configuration_id=configuration AND kind='email';
    SELECT testing_recipient INTO testing FROM stewardship_system_configuration;
    SELECT m.mode,jsonb_build_array(recipient.address) INTO mode,intended
        FROM stewardship_outbox_message m
        JOIN stewardship_daily_digest_recipient recipient ON recipient.outbox_id=m.id
        WHERE m.id=message AND m.purpose='daily_digest';
    IF email IS NULL OR mode IS NULL OR jsonb_array_length(intended)<>1 THEN
        RAISE EXCEPTION 'Daily retry requires exact configured routing' USING ERRCODE='23514'; END IF;
    routed:=CASE mode WHEN 'testing' THEN jsonb_build_array(testing) ELSE intended END;
    IF mode='testing' THEN
        heading:='[TEST] '||heading;
        body:='TEST — pending report is routed only to the Testing recipient. '||body;
    END IF;
    content:=jsonb_build_object('sender',email->>'sender','reply_to',email->>'reply_to',
        'intended_recipients',intended,'routed_recipients',routed,'subject',heading,
        'html',CASE mode WHEN 'testing' THEN '<h2>TEST</h2>' ELSE '' END||'<p>'||body||'</p>',
        'text',body);
    RETURN content||jsonb_build_object('configuration_id',configuration,'template_id',NULL,
        'payload_digest',encode(sha256(convert_to(content::text,'UTF8')),'hex'));
END $$;
REVOKE ALL ON FUNCTION stewardship_daily_digest_seed_v1(uuid,uuid) FROM PUBLIC;

CREATE FUNCTION stewardship_daily_dispatch_render_v1(proposed jsonb,message uuid)
RETURNS boolean LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT EXISTS(SELECT 1 FROM stewardship_outbox_message m
        JOIN stewardship_daily_digest_recipient recipient ON recipient.outbox_id=m.id AND recipient.id=m.semantic_key
        JOIN stewardship_daily_digest_ready ready ON ready.id=recipient.ready_id
        JOIN stewardship_daily_digest_snapshot s ON s.id=ready.snapshot_id
        JOIN stewardship_daily_digest_preparation p ON p.id=s.preparation_id
        JOIN stewardship_schedule_revision revision ON revision.id=p.revision_id
        CROSS JOIN stewardship_system_configuration runtime
        JOIN stewardship_content_version content ON content.configuration_id=runtime.active_configuration_id
            AND content.record_id=(revision.values->>'template_version')::uuid
            AND content.campaign_id=p.campaign_id AND content.kind='email'
        JOIN stewardship_applied_integration email ON email.configuration_id=runtime.active_configuration_id AND email.kind='email'
        WHERE m.id=$2 AND (proposed->>'message_id')::uuid=m.id
          AND (proposed->>'configuration_id')::uuid=runtime.active_configuration_id
          AND (proposed->>'template_id')::uuid=content.id
          AND proposed->>'sender'=email.settings->>'sender'
          AND proposed->>'reply_to'=email.settings->>'reply_to'
          AND proposed->'intended_recipients'=jsonb_build_array(recipient.address)
          AND proposed->'routed_recipients'=CASE WHEN m.mode='testing'
              THEN jsonb_build_array(runtime.testing_recipient) ELSE jsonb_build_array(recipient.address) END
          AND right(proposed->>'html',length(ready.html))=ready.html
          AND right(proposed->>'text',length(ready.text))=ready.text
          AND length(proposed->>'subject') BETWEEN 1 AND 254
          AND (m.mode<>'testing' OR (starts_with(proposed->>'subject','[TEST] ')
              AND starts_with(proposed->>'html','<h2>TEST</h2><p>')
              AND starts_with(proposed->>'text','TEST — sent to '||runtime.testing_recipient||' instead of Administrator '||recipient.address||'.'))))
$$;

CREATE FUNCTION stewardship_daily_dispatch_write_v1(relation_name text,proposed jsonb,prior jsonb)
RETURNS boolean LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE task stewardship_task_run%ROWTYPE; message stewardship_outbox_message%ROWTYPE;
BEGIN
    IF relation_name NOT IN ('stewardship_outbox_message','stewardship_outbox_render','stewardship_outbox_event')
      OR NOT EXISTS(SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
        AND mode='ExclusiveLock' AND granted) THEN RETURN false; END IF;
    SELECT * INTO task FROM stewardship_task_run WHERE id=(proposed->>'correlation_id')::uuid;
    SELECT * INTO message FROM stewardship_outbox_message
        WHERE id=task.domain_request_id AND task_id=task.root_id AND purpose='daily_digest';
    IF task.id IS NULL OR task.task_type<>'outbox_delivery' OR message.id IS NULL
      OR NOT EXISTS(SELECT 1 FROM stewardship_daily_digest_recipient recipient
        JOIN stewardship_daily_digest_ready ready ON ready.id=recipient.ready_id
        JOIN stewardship_daily_digest_snapshot s ON s.id=ready.snapshot_id
        JOIN stewardship_daily_digest_preparation p ON p.id=s.preparation_id
        WHERE recipient.outbox_id=message.id AND recipient.id=message.semantic_key
          AND message.campaign_id=p.campaign_id AND message.mode=p.mode)
      OR (CASE WHEN relation_name='stewardship_outbox_message' THEN proposed->>'id'
            ELSE proposed->>'message_id' END)::uuid IS DISTINCT FROM message.id
    THEN RETURN false; END IF;
    IF task.state='abandoned' THEN
        RETURN task.id=message.run_id AND task.fence>=message.task_fence
          AND message.provider_deadline<=clock_timestamp() AND proposed->>'actor_id' IS NOT NULL
          AND proposed->>'action'='mark_unknown' AND proposed->>'reason'='recovery_unknown'
          AND ((relation_name='stewardship_outbox_message' AND prior->>'state'='submitting')
            OR (relation_name='stewardship_outbox_event' AND message.state='delivery_unknown'));
    END IF;
    IF task.state<>'running' OR task.worker_id IS DISTINCT FROM (proposed->>'actor_id')::uuid
      OR task.lease_expires_at<=clock_timestamp() THEN RETURN false; END IF;
    IF relation_name='stewardship_outbox_message' THEN
        IF prior IS NULL THEN RETURN false; END IF;
        IF proposed->>'action' IN ('prepared','submit') THEN
            RETURN stewardship_daily_dispatch_live_v1(message.id) AND EXISTS(
                SELECT 1 FROM stewardship_outbox_render rendering WHERE rendering.id=(proposed->>'render_id')::uuid
                    AND stewardship_daily_dispatch_render_v1(to_jsonb(rendering),message.id));
        ELSIF proposed->>'action' IN ('accept','retry_unaccepted','fail_unaccepted','mark_unknown') THEN
            RETURN prior->>'state'='submitting' AND (prior->>'run_id')::uuid=task.id
              AND (prior->>'task_fence')::bigint=task.fence AND (prior->>'worker_id')::uuid=task.worker_id
              AND proposed->>'reason' LIKE 'smtp_%';
        END IF;
        RETURN proposed->>'action' IN ('cancel_unsent','hold','release_hold');
    ELSIF relation_name='stewardship_outbox_render' THEN
        RETURN prior IS NULL AND message.state IN ('pending','retry_wait')
          AND stewardship_daily_dispatch_live_v1(message.id)
          AND stewardship_daily_dispatch_render_v1(proposed,message.id);
    END IF;
    RETURN relation_name='stewardship_outbox_event' AND prior IS NULL
        AND proposed->>'action'=message.action AND (proposed->>'version')::bigint=message.version;
END $$;

-- Completion is derived from the entire immutable cohort, never one child's
-- outcome. It has no provider authority and may truthfully settle observations
-- after a pause/restore gate, just as an in-flight Family attempt may settle.
CREATE VIEW stewardship_daily_digest_completion_ready AS
    SELECT p.id AS preparation_id FROM stewardship_daily_digest_preparation p
        JOIN stewardship_daily_digest_snapshot s ON s.preparation_id=p.id
        JOIN stewardship_daily_digest_ready ready ON ready.snapshot_id=s.id
        WHERE p.phase='complete' AND jsonb_array_length(ready.recipients)>0
          AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements_text(ready.recipients) AS desired(address)
            WHERE NOT EXISTS(SELECT 1 FROM stewardship_daily_digest_recipient recipient
                LEFT JOIN stewardship_outbox_message m ON m.id=recipient.outbox_id
                WHERE recipient.ready_id=ready.id AND recipient.address=desired.address
                  AND (m.state='delivered' OR (recipient.outbox_id IS NULL
                      AND jsonb_array_length(recipient.covered_messages)>0
                      AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements_text(recipient.covered_messages) AS referenced(message_id)
                          WHERE NOT EXISTS(SELECT 1 FROM stewardship_outbox_message accepted
                              WHERE accepted.id=referenced.message_id::uuid AND accepted.state='delivered'))))));
REVOKE ALL ON stewardship_daily_digest_completion_ready FROM PUBLIC;

CREATE FUNCTION stewardship_daily_digest_delivered_v1(preparation uuid)
RETURNS boolean LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT EXISTS(SELECT 1 FROM stewardship_daily_digest_completion_ready WHERE preparation_id=$1)
$$;

CREATE FUNCTION stewardship_daily_digest_completion_v1(proposed jsonb)
RETURNS boolean LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT EXISTS(SELECT 1 FROM stewardship_daily_digest_preparation p
        JOIN stewardship_schedule_occurrence o ON o.id=p.occurrence_id
        JOIN stewardship_task_run t ON t.id=(proposed->>'task_id')::uuid
        WHERE o.id=(proposed->>'id')::uuid AND o.state IN ('pending','running')
          AND proposed->>'state' IN ('running','succeeded')
          AND proposed->>'reason'='daily_digest_complete'
          AND (proposed->>'definition_id')::uuid=p.definition_id
          AND (proposed->>'revision_id')::uuid=p.revision_id AND proposed->>'mode'=p.mode
          AND proposed->>'target'='admins' AND proposed->>'outbox_id' IS NULL
          AND (proposed->>'actor_id')::uuid=t.worker_id AND (proposed->>'worker_id')::uuid=t.worker_id
          AND (proposed->>'correlation_id')::uuid=t.id AND (proposed->>'fence')::bigint=t.fence
          AND stewardship_fact_live(t.id,t.fence,t.worker_id)
          AND ((t.task_type='daily_digest_prepare' AND t.root_id=p.task_id AND t.domain_request_id=p.id)
            OR (t.task_type='daily_digest_finalize' AND t.domain_request_id=p.id AND EXISTS(
                SELECT 1 FROM stewardship_task_run root WHERE root.id=t.root_id
                  AND root.task_type=t.task_type AND root.domain_request_id=p.id
                  AND root.idempotency_key=p.id::text))
            OR (t.task_type='outbox_delivery' AND EXISTS(
                SELECT 1 FROM stewardship_outbox_message m
                JOIN stewardship_daily_digest_recipient recipient ON recipient.outbox_id=m.id
                JOIN stewardship_daily_digest_ready ready ON ready.id=recipient.ready_id
                JOIN stewardship_daily_digest_snapshot s ON s.id=ready.snapshot_id
                WHERE m.id=t.domain_request_id AND m.task_id=t.root_id AND m.state='delivered'
                  AND m.run_id=t.id AND m.task_fence=t.fence AND m.worker_id=t.worker_id
                  AND s.preparation_id=p.id)))
          AND stewardship_daily_digest_delivered_v1(p.id))
$$;

CREATE FUNCTION stewardship_daily_digest_settle_v1() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE p stewardship_daily_digest_preparation%ROWTYPE;
    occurrence stewardship_schedule_occurrence%ROWTYPE;
    task stewardship_task_run%ROWTYPE;
    proposed jsonb;
BEGIN
    PERFORM pg_advisory_xact_lock(736220,1);
    IF TG_TABLE_NAME='stewardship_daily_digest_preparation' THEN
        SELECT * INTO p FROM stewardship_daily_digest_preparation WHERE id=NEW.id;
        SELECT * INTO task FROM stewardship_task_run WHERE id=p.run_id;
    ELSIF TG_TABLE_NAME='stewardship_task_run' THEN
        SELECT * INTO p FROM stewardship_daily_digest_preparation WHERE id=NEW.domain_request_id;
        SELECT * INTO task FROM stewardship_task_run WHERE id=NEW.id;
    ELSE
        SELECT preparation.* INTO p FROM stewardship_daily_digest_recipient recipient
            JOIN stewardship_daily_digest_ready ready ON ready.id=recipient.ready_id
            JOIN stewardship_daily_digest_snapshot s ON s.id=ready.snapshot_id
            JOIN stewardship_daily_digest_preparation preparation ON preparation.id=s.preparation_id
            WHERE recipient.outbox_id=NEW.id;
        SELECT * INTO task FROM stewardship_task_run WHERE id=NEW.run_id;
    END IF;
    IF p.id IS NULL OR NOT stewardship_daily_digest_delivered_v1(p.id) THEN RETURN NULL; END IF;
    SELECT * INTO occurrence FROM stewardship_schedule_occurrence WHERE id=p.occurrence_id FOR UPDATE;
    IF occurrence.state<>'pending' THEN RETURN NULL; END IF;
    proposed:=to_jsonb(occurrence)||jsonb_build_object('state','running','reason','daily_digest_complete',
        'task_id',task.id,'worker_id',task.worker_id,'actor_id',task.worker_id,
        'correlation_id',task.id,'fence',task.fence);
    IF NOT stewardship_daily_digest_completion_v1(proposed) THEN
        -- A later Admin/provider reconciliation can accept an abandoned attempt.
        -- Do not roll back that truthful observation to fabricate a live owner;
        -- the compiled metadata finalizer acquires a fresh claim separately.
        RETURN NULL;
    END IF;
    -- Preserve the public occurrence transition graph. Neither running nor
    -- succeeded is exposed before this atomic, all-recipient completion proof.
    UPDATE stewardship_schedule_occurrence SET state='running',reason='daily_digest_complete',
        task_id=task.id,worker_id=task.worker_id,actor_id=task.worker_id,
        correlation_id=task.id,fence=task.fence,heartbeat_at=clock_timestamp(),
        lease_expires_at=task.lease_expires_at,attempts=attempts+1,version=version+1
        WHERE id=occurrence.id;
    UPDATE stewardship_schedule_occurrence SET state='succeeded',lease_expires_at=NULL,version=version+1
        WHERE id=occurrence.id;
    INSERT INTO stewardship_schedule_fulfillment(id,definition_id,mode,target,slot,disposition,occurrence_id,actor_id,correlation_id)
        VALUES(gen_random_uuid(),occurrence.definition_id,occurrence.mode,occurrence.target,occurrence.slot,
            'delivered',occurrence.id,task.worker_id,task.id);
    RETURN NULL;
END $$;
REVOKE ALL ON FUNCTION stewardship_daily_digest_settle_v1() FROM PUBLIC;
CREATE CONSTRAINT TRIGGER daily_digest_cohort_complete AFTER UPDATE ON stewardship_daily_digest_preparation
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW WHEN (NEW.phase='complete')
EXECUTE FUNCTION stewardship_daily_digest_settle_v1();
CREATE CONSTRAINT TRIGGER daily_digest_late_completion AFTER UPDATE ON stewardship_task_run
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW WHEN (NEW.task_type='daily_digest_finalize' AND NEW.state='running' AND NEW.action='claim')
EXECUTE FUNCTION stewardship_daily_digest_settle_v1();
CREATE CONSTRAINT TRIGGER daily_digest_delivery_complete AFTER UPDATE ON stewardship_outbox_message
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW WHEN (NEW.purpose='daily_digest' AND NEW.state='delivered')
EXECUTE FUNCTION stewardship_daily_digest_settle_v1();
