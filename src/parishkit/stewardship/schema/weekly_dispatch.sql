-- Weekly messages share the isolated MAIL claim and SMTP evidence protocol, but
-- never inherit Family credential reads, refusal propagation or group writes.
CREATE FUNCTION stewardship_weekly_dispatch_live_v1(message uuid)
RETURNS boolean LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT EXISTS(SELECT 1 FROM stewardship_outbox_message m
        JOIN stewardship_weekly_digest_recipient recipient ON recipient.outbox_id=m.id AND recipient.id=m.semantic_key
        JOIN stewardship_weekly_digest_snapshot s ON s.id=recipient.snapshot_id
        JOIN stewardship_weekly_digest_preparation p ON p.id=s.preparation_id
        JOIN stewardship_schedule_occurrence o ON o.id=p.occurrence_id
        JOIN stewardship_campaign c ON c.id=p.campaign_id
        JOIN stewardship_system_configuration runtime ON runtime.current_campaign_id=c.id
        JOIN stewardship_address_rule a ON a.configuration_id=runtime.active_configuration_id
            AND a.email=recipient.address AND a.roles @> '["administrator"]'::jsonb
        WHERE m.id=$1 AND m.purpose='weekly_digest' AND m.credential_namespace='none' AND m.family_id IS NULL
          AND m.campaign_id=p.campaign_id AND m.mode=p.mode AND p.phase='complete'
          AND o.state IN ('pending','running') AND o.due_at<=stewardship_campaign_now_v1()
          AND (NOT (m.mode='production' AND c.delivery_paused) OR stewardship_delivery_message_released_v1(m.id))
          AND stewardship_weekly_digest_scope_v1(p.campaign_id,p.revision_id,p.campaign_configuration_id,p.mode,p.rehearsal_epoch_id)
          AND NOT stewardship_schedule_slot_excluded_v1(o.definition_id,o.mode,o.target,o.slot))
$$;

-- Web requests a retry but cannot copy private report content into an arbitrary
-- render. MAIL must replace this fixed, non-sendable seed under its live claim.
CREATE FUNCTION stewardship_weekly_digest_seed_v1(message uuid,configuration uuid)
RETURNS jsonb LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE email jsonb; intended jsonb; routed jsonb; mode text; testing text;
    content jsonb; heading text:='Weekly report awaiting preparation';
    body text:='PARISHKIT_PENDING_WEEKLY_DIGEST: Weekly report awaiting preparation.';
BEGIN
    SELECT settings INTO email FROM stewardship_applied_integration
        WHERE configuration_id=configuration AND kind='email';
    SELECT testing_recipient INTO testing FROM stewardship_system_configuration;
    SELECT m.mode,jsonb_build_array(recipient.address) INTO mode,intended
        FROM stewardship_outbox_message m
        JOIN stewardship_weekly_digest_recipient recipient ON recipient.outbox_id=m.id
        WHERE m.id=message AND m.purpose='weekly_digest';
    IF email IS NULL OR mode IS NULL OR jsonb_array_length(intended)<>1 THEN
        RAISE EXCEPTION 'Weekly retry requires exact configured routing' USING ERRCODE='23514'; END IF;
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
REVOKE ALL ON FUNCTION stewardship_weekly_digest_seed_v1(uuid,uuid) FROM PUBLIC;

CREATE FUNCTION stewardship_weekly_dispatch_render_v1(proposed jsonb,message uuid)
RETURNS boolean LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT EXISTS(SELECT 1 FROM stewardship_outbox_message m
        JOIN stewardship_weekly_digest_recipient recipient ON recipient.outbox_id=m.id AND recipient.id=m.semantic_key
        JOIN stewardship_weekly_digest_snapshot s ON s.id=recipient.snapshot_id
        JOIN stewardship_weekly_digest_preparation p ON p.id=s.preparation_id
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
          AND right(proposed->>'html',length(recipient.html))=recipient.html
          AND right(proposed->>'text',length(recipient.text))=recipient.text
          AND length(proposed->>'subject') BETWEEN 1 AND 254
          AND (m.mode<>'testing' OR (starts_with(proposed->>'subject','[TEST] ')
              AND starts_with(proposed->>'html','<h2>TEST</h2><p>')
              AND starts_with(proposed->>'text','TEST — sent to '||runtime.testing_recipient||' instead of Administrator '||recipient.address||'.'))))
$$;

CREATE FUNCTION stewardship_weekly_dispatch_write_v1(relation_name text,proposed jsonb,prior jsonb)
RETURNS boolean LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE task stewardship_task_run%ROWTYPE; message stewardship_outbox_message%ROWTYPE;
BEGIN
    IF relation_name NOT IN ('stewardship_outbox_message','stewardship_outbox_render','stewardship_outbox_event')
      OR NOT EXISTS(SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
        AND mode='ExclusiveLock' AND granted) THEN RETURN false; END IF;
    SELECT * INTO task FROM stewardship_task_run WHERE id=(proposed->>'correlation_id')::uuid;
    SELECT * INTO message FROM stewardship_outbox_message
        WHERE id=task.domain_request_id AND task_id=task.root_id AND purpose='weekly_digest';
    IF task.id IS NULL OR task.task_type<>'outbox_delivery' OR message.id IS NULL
      OR NOT EXISTS(SELECT 1 FROM stewardship_weekly_digest_recipient recipient
        JOIN stewardship_weekly_digest_snapshot s ON s.id=recipient.snapshot_id
        JOIN stewardship_weekly_digest_preparation p ON p.id=s.preparation_id
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
            RETURN stewardship_weekly_dispatch_live_v1(message.id) AND EXISTS(
                SELECT 1 FROM stewardship_outbox_render rendering WHERE rendering.id=(proposed->>'render_id')::uuid
                    AND stewardship_weekly_dispatch_render_v1(to_jsonb(rendering),message.id));
        ELSIF proposed->>'action' IN ('accept','retry_unaccepted','fail_unaccepted','mark_unknown') THEN
            RETURN prior->>'state'='submitting' AND (prior->>'run_id')::uuid=task.id
              AND (prior->>'task_fence')::bigint=task.fence AND (prior->>'worker_id')::uuid=task.worker_id
              AND proposed->>'reason' LIKE 'smtp_%';
        END IF;
        IF proposed->>'action'='cancel_unsent' AND proposed->>'reason'='recipient_revoked' THEN
            RETURN NOT EXISTS(SELECT 1 FROM stewardship_weekly_digest_recipient recipient
                CROSS JOIN stewardship_system_configuration runtime
                JOIN stewardship_address_rule a ON a.configuration_id=runtime.active_configuration_id
                    AND a.roles @> '["administrator"]'::jsonb
                WHERE recipient.outbox_id=message.id AND a.email=recipient.address);
        END IF;
        RETURN proposed->>'action' IN ('cancel_unsent','hold','release_hold');
    ELSIF relation_name='stewardship_outbox_render' THEN
        RETURN prior IS NULL AND message.state IN ('pending','retry_wait')
          AND stewardship_weekly_dispatch_live_v1(message.id)
          AND stewardship_weekly_dispatch_render_v1(proposed,message.id);
    END IF;
    RETURN relation_name='stewardship_outbox_event' AND prior IS NULL
        AND proposed->>'action'=message.action AND (proposed->>'version')::bigint=message.version;
END $$;
