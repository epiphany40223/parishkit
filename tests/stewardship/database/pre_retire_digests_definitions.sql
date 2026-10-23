-- The digest write functions and triggers migration 0171 (#1834) drops,
-- copied unchanged from the baseline schema files before it. While the
-- users swap is pending, test_users_swap_postgresql.py makes them again
-- (``before_retire_digests``), as the users upgrade's base has them; the
-- base's gate bodies still call the write admissions. Never edit: it is
-- the history those bodies ran against.

-- From schema/daily_digests.sql.
CREATE FUNCTION stewardship_daily_digest_write_admitted_v1(
    relation_name text,proposed jsonb,prior jsonb
) RETURNS boolean LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE p stewardship_daily_digest_preparation%ROWTYPE;
        o stewardship_schedule_occurrence%ROWTYPE;
        selected stewardship_schedule_occurrence%ROWTYPE;
BEGIN
    IF relation_name NOT IN ('stewardship_schedule_occurrence','stewardship_occurrence_transition',
        'stewardship_outbox_message','stewardship_outbox_render','stewardship_outbox_event')
    THEN RETURN false; END IF;
    IF NOT EXISTS(SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
        AND mode='ExclusiveLock' AND granted) THEN RETURN false; END IF;
    IF relation_name='stewardship_occurrence_transition' THEN
        SELECT * INTO o FROM stewardship_schedule_occurrence WHERE id=(proposed->>'occurrence_id')::uuid;
        RETURN prior IS NULL AND o.id IS NOT NULL
          AND (proposed->>'version')::bigint=o.version AND proposed->>'after_state'=o.state
          AND (proposed->>'actor_id')::uuid=o.actor_id
          AND (proposed->>'correlation_id')::uuid=o.correlation_id
          AND stewardship_daily_digest_write_admitted_v1('stewardship_schedule_occurrence',to_jsonb(o),
              CASE WHEN o.state='pending' THEN NULL ELSE to_jsonb(o)||jsonb_build_object('state','pending') END);
    END IF;
    SELECT preparation.* INTO p FROM stewardship_daily_digest_preparation preparation
      JOIN stewardship_task_run t ON t.root_id=preparation.task_id
      JOIN stewardship_task_event e ON e.run_id=t.id
      WHERE e.id=(proposed->>'correlation_id')::uuid AND e.action='claim' AND e.state='running'
        AND e.worker_id=(proposed->>'actor_id')::uuid AND e.fence=t.fence
        AND stewardship_daily_digest_live_v1(preparation.id,t.id,e.fence,e.worker_id);
    IF p.id IS NULL THEN RETURN false; END IF;
    IF relation_name IN ('stewardship_outbox_message','stewardship_outbox_render','stewardship_outbox_event') THEN
        RETURN stewardship_daily_digest_mail_write_v1(p.id,relation_name,proposed,prior);
    END IF;
    IF proposed->>'target'<>'admins'
      OR (proposed->>'definition_id')::uuid<>p.definition_id
      OR (proposed->>'revision_id')::uuid<>p.revision_id OR proposed->>'mode'<>p.mode
      OR (proposed->>'due_at')::timestamptz>p.cutoff
      OR proposed->>'task_id' IS NOT NULL OR proposed->>'outbox_id' IS NOT NULL
    THEN RETURN false; END IF;
    IF prior IS NULL THEN
        IF p.phase='cover' AND proposed->>'slot'='recovery:'||p.id::text THEN
            RETURN proposed->>'state'='pending' AND p.occurrence_id IS NULL
                AND (proposed->>'due_at')::timestamptz=(SELECT max(candidate.due_at)
                    FROM stewardship_schedule_occurrence candidate WHERE candidate.id IN (
                        SELECT stewardship_daily_digest_predecessors_v1(p.definition_id,p.mode,p.rehearsal_epoch_id)))
                AND NOT EXISTS(SELECT 1 FROM stewardship_schedule_occurrence candidate
                    WHERE candidate.revision_id=p.revision_id AND candidate.mode=p.mode AND candidate.target='admins'
                      AND candidate.slot='recovery:'||p.id::text
                      AND candidate.id<>(proposed->>'id')::uuid);
        END IF;
        RETURN p.phase='dates' AND proposed->>'state'='pending'
          AND stewardship_catchup_due_v1(p.definition_id,proposed->>'slot') IS NOT NULL
          AND (proposed->>'due_at')::timestamptz=stewardship_catchup_due_v1(p.definition_id,proposed->>'slot');
    END IF;
    IF p.phase<>'cover' OR prior->>'state'<>'pending' OR proposed->>'state'<>'coalesced'
      OR proposed->>'reason'<>'missed_daily_recovery'
      OR prior->>'task_id' IS NOT NULL OR prior->>'outbox_id' IS NOT NULL
      OR EXISTS(SELECT 1 FROM stewardship_daily_digest_preparation other
          WHERE other.id<>p.id AND other.occurrence_id=(prior->>'id')::uuid)
    THEN RETURN false; END IF;
    SELECT * INTO selected FROM stewardship_schedule_occurrence WHERE id=(proposed->>'replacement_id')::uuid;
    RETURN selected.id IS NOT NULL AND selected.state='pending'
      AND selected.definition_id=p.definition_id AND selected.revision_id=p.revision_id
      AND selected.mode=p.mode AND selected.target='admins'
      AND selected.task_id IS NULL AND selected.outbox_id IS NULL
      AND selected.due_at BETWEEN (proposed->>'due_at')::timestamptz AND p.cutoff
      AND NOT stewardship_schedule_slot_excluded_v1(selected.definition_id,selected.mode,selected.target,selected.slot)
      AND NOT EXISTS(SELECT 1 FROM stewardship_daily_digest_preparation other
          WHERE other.id<>p.id AND other.occurrence_id=selected.id);
END $$;

CREATE FUNCTION stewardship_daily_digest_source_commit_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM stewardship_source_pin WHERE snapshot_id=NEW.source_id
        AND parent_kind='digest' AND parent_id=NEW.id AND expires_at IS NULL) THEN
        RAISE EXCEPTION 'Daily snapshot requires retained source protection' USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER daily_digest_snapshot_complete AFTER INSERT ON stewardship_daily_digest_snapshot
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION stewardship_daily_digest_source_commit_v1();

CREATE FUNCTION stewardship_daily_digest_fact_commit_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM stewardship_fact_pin WHERE fact_set_id=NEW.fact_set_id
        AND parent_kind='digest' AND parent_id=NEW.snapshot_id) THEN
        RAISE EXCEPTION 'Daily compilation requires retained fact protection' USING ERRCODE='23514';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM stewardship_daily_digest_snapshot s
        JOIN stewardship_daily_digest_preparation p ON p.id=s.preparation_id
        WHERE s.id=NEW.snapshot_id AND p.phase IN ('fanout','complete')) THEN
        RAISE EXCEPTION 'Daily report protection must release its fanout atomically' USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER daily_digest_ready_complete AFTER INSERT ON stewardship_daily_digest_ready
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION stewardship_daily_digest_fact_commit_v1();

CREATE FUNCTION stewardship_daily_digest_mail_write_v1(
    preparation uuid,relation_name text,proposed jsonb,prior jsonb
) RETURNS boolean LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE p stewardship_daily_digest_preparation%ROWTYPE;
        r stewardship_daily_digest_ready%ROWTYPE;
        m stewardship_outbox_message%ROWTYPE;
        runtime stewardship_system_configuration%ROWTYPE;
        template uuid; email jsonb;
BEGIN
    -- The caller has already verified the exact live claim event and work lock.
    -- This helper admits allocation only, never later provider state changes.
    SELECT * INTO p FROM stewardship_daily_digest_preparation WHERE id=preparation;
    SELECT ready.* INTO r FROM stewardship_daily_digest_ready ready
        JOIN stewardship_daily_digest_snapshot s ON s.id=ready.snapshot_id
        WHERE s.preparation_id=p.id;
    IF prior IS NOT NULL OR p.phase<>'fanout' OR r.id IS NULL THEN RETURN false; END IF;
    IF relation_name='stewardship_outbox_message' THEN
        RETURN proposed->>'purpose'='daily_digest' AND proposed->>'mode'=p.mode
            AND (proposed->>'scope_id')::uuid=p.campaign_id
            AND (proposed->>'campaign_id')::uuid=p.campaign_id
            AND proposed->>'family_id' IS NULL AND proposed->>'credential_namespace'='none'
            AND proposed->>'state'='pending' AND proposed->>'action'='created'
            AND (proposed->>'version')::bigint=1;
    END IF;
    SELECT * INTO m FROM stewardship_outbox_message WHERE id=(proposed->>'message_id')::uuid;
    IF m.id IS NULL OR m.purpose<>'daily_digest' OR m.campaign_id<>p.campaign_id
        OR m.mode<>p.mode OR m.state<>'pending' OR m.version<>1
        OR m.correlation_id IS DISTINCT FROM (proposed->>'correlation_id')::uuid
        OR m.actor_id IS DISTINCT FROM (proposed->>'actor_id')::uuid THEN RETURN false; END IF;
    IF relation_name='stewardship_outbox_event' THEN
        RETURN proposed->>'action'='created' AND proposed->>'state'='pending'
            AND (proposed->>'version')::bigint=1;
    END IF;
    IF relation_name<>'stewardship_outbox_render' THEN RETURN false; END IF;
    SELECT * INTO runtime FROM stewardship_system_configuration;
    SELECT content.id INTO template FROM stewardship_content_version content
        JOIN stewardship_schedule_revision revision ON revision.id=p.revision_id
        WHERE content.configuration_id=runtime.active_configuration_id
          AND content.campaign_id=p.campaign_id AND content.kind='email'
          AND content.record_id=(revision.values->>'template_version')::uuid;
    SELECT settings INTO email FROM stewardship_applied_integration
        WHERE configuration_id=runtime.active_configuration_id AND kind='email';
    RETURN (proposed->>'id')::uuid=m.render_id
        AND (proposed->>'configuration_id')::uuid=runtime.active_configuration_id
        AND (proposed->>'template_id')::uuid=template
        AND proposed->>'sender'=email->>'sender' AND proposed->>'reply_to'=email->>'reply_to'
        AND jsonb_array_length(proposed->'intended_recipients')=1
        AND r.recipients ? (proposed->'intended_recipients'->>0)
        AND proposed->'routed_recipients'=CASE p.mode WHEN 'testing'
            THEN jsonb_build_array(runtime.testing_recipient) ELSE proposed->'intended_recipients' END
        AND right(proposed->>'html',length(r.html))=r.html
        AND right(proposed->>'text',length(r.text))=r.text;
END $$;

CREATE FUNCTION stewardship_daily_digest_mail_commit_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF NEW.purpose='daily_digest' AND EXISTS(
        SELECT 1 FROM stewardship_task_event e JOIN stewardship_task_run t ON t.id=e.run_id
        WHERE e.id=NEW.correlation_id AND t.task_type='daily_digest_prepare')
      AND NOT EXISTS(SELECT 1 FROM stewardship_daily_digest_recipient
          WHERE id=NEW.semantic_key AND outbox_id=NEW.id
            AND actor_id=NEW.actor_id AND correlation_id=NEW.correlation_id) THEN
        RAISE EXCEPTION 'Daily message requires atomic recipient ownership' USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END $$;
REVOKE ALL ON FUNCTION stewardship_daily_digest_mail_commit_v1() FROM PUBLIC;
CREATE CONSTRAINT TRIGGER daily_digest_mail_complete AFTER INSERT ON stewardship_outbox_message
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION stewardship_daily_digest_mail_commit_v1();


-- From schema/daily_dispatch.sql.
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
        IF proposed->>'action'='cancel_unsent' AND proposed->>'reason'='recipient_revoked' THEN
            RETURN NOT EXISTS(SELECT 1 FROM stewardship_daily_digest_recipient recipient
                CROSS JOIN stewardship_system_configuration runtime
                JOIN stewardship_address_rule a ON a.configuration_id=runtime.active_configuration_id
                    AND a.roles @> '["administrator"]'::jsonb
                WHERE recipient.outbox_id=message.id AND a.email=recipient.address);
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


CREATE FUNCTION stewardship_daily_digest_resolved_v1(preparation uuid)
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
          AND EXISTS(SELECT 1 FROM stewardship_daily_digest_completion_ready proof
              WHERE proof.preparation_id=p.id AND proposed->>'reason'=CASE proof.disposition
                  WHEN 'empty' THEN 'daily_digest_no_current_recipients' ELSE 'daily_digest_complete' END)
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
                WHERE m.id=t.domain_request_id AND m.task_id=t.root_id
                  AND ((m.state='delivered' AND m.run_id=t.id AND m.task_fence=t.fence AND m.worker_id=t.worker_id)
                    OR (m.state='cancelled' AND m.reason='recipient_revoked'
                      AND m.correlation_id=t.id AND m.actor_id=t.worker_id))
                  AND s.preparation_id=p.id)))
          AND stewardship_daily_digest_resolved_v1(p.id))
$$;

CREATE FUNCTION stewardship_daily_digest_settle_v1() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE p stewardship_daily_digest_preparation%ROWTYPE;
    occurrence stewardship_schedule_occurrence%ROWTYPE;
    task stewardship_task_run%ROWTYPE;
    proposed jsonb; outcome text; v_reason text;
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
        SELECT * INTO task FROM stewardship_task_run WHERE id=CASE
            WHEN NEW.state='cancelled' THEN NEW.correlation_id ELSE NEW.run_id END;
    END IF;
    SELECT disposition INTO outcome FROM stewardship_daily_digest_completion_ready WHERE preparation_id=p.id;
    IF p.id IS NULL OR outcome IS NULL THEN RETURN NULL; END IF;
    v_reason:=CASE outcome WHEN 'empty' THEN 'daily_digest_no_current_recipients' ELSE 'daily_digest_complete' END;
    SELECT * INTO occurrence FROM stewardship_schedule_occurrence WHERE id=p.occurrence_id FOR UPDATE;
    IF occurrence.state<>'pending' THEN RETURN NULL; END IF;
    proposed:=to_jsonb(occurrence)||jsonb_build_object('state','running','reason',v_reason,
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
    UPDATE stewardship_schedule_occurrence SET state='running',reason=v_reason,
        task_id=task.id,worker_id=task.worker_id,actor_id=task.worker_id,
        correlation_id=task.id,fence=task.fence,heartbeat_at=clock_timestamp(),
        lease_expires_at=task.lease_expires_at,attempts=attempts+1,version=version+1
        WHERE id=occurrence.id;
    UPDATE stewardship_schedule_occurrence SET state='succeeded',lease_expires_at=NULL,version=version+1
        WHERE id=occurrence.id;
    INSERT INTO stewardship_schedule_fulfillment(id,definition_id,mode,target,slot,disposition,occurrence_id,actor_id,correlation_id)
        VALUES(gen_random_uuid(),occurrence.definition_id,occurrence.mode,occurrence.target,occurrence.slot,
            outcome,occurrence.id,task.worker_id,task.id);
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
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW WHEN (NEW.purpose='daily_digest'
    AND (NEW.state='delivered' OR (NEW.state='cancelled' AND NEW.reason='recipient_revoked')))
EXECUTE FUNCTION stewardship_daily_digest_settle_v1();

-- From schema/weekly_capture.sql.
CREATE FUNCTION stewardship_weekly_snapshot_commit_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM stewardship_weekly_digest_preparation
        WHERE id=NEW.preparation_id AND phase IN ('fanout','complete')) THEN
        RAISE EXCEPTION 'Weekly capture and fanout release must commit together' USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER weekly_snapshot_complete AFTER INSERT ON stewardship_weekly_digest_snapshot
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION stewardship_weekly_snapshot_commit_v1();


-- From schema/weekly_completion.sql.

CREATE FUNCTION stewardship_weekly_digest_resolved_v1(preparation uuid)
RETURNS boolean LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT EXISTS(SELECT 1 FROM stewardship_weekly_digest_completion_ready WHERE preparation_id=$1)
$$;

CREATE FUNCTION stewardship_weekly_digest_completion_v1(proposed jsonb)
RETURNS boolean LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT EXISTS(SELECT 1 FROM stewardship_weekly_digest_preparation p
      JOIN stewardship_schedule_occurrence o ON o.id=p.occurrence_id
      JOIN stewardship_task_run t ON t.id=(proposed->>'task_id')::uuid
      JOIN stewardship_weekly_digest_completion_ready proof ON proof.preparation_id=p.id
      WHERE o.id=(proposed->>'id')::uuid AND o.state IN ('pending','running')
        AND proposed->>'state' IN ('running','succeeded')
        AND proposed->>'reason'=CASE proof.disposition WHEN 'empty' THEN proof.empty_reason
            ELSE 'weekly_digest_complete' END
        AND (proposed->>'definition_id')::uuid=p.definition_id
        AND (proposed->>'revision_id')::uuid=p.revision_id AND proposed->>'mode'=p.mode
        AND proposed->>'target'='admins' AND proposed->>'outbox_id' IS NULL
        AND (proposed->>'actor_id')::uuid=t.worker_id AND (proposed->>'worker_id')::uuid=t.worker_id
        AND (proposed->>'correlation_id')::uuid=t.id AND (proposed->>'fence')::bigint=t.fence
        AND stewardship_fact_live(t.id,t.fence,t.worker_id)
        AND ((t.task_type='weekly_digest_prepare' AND t.root_id=p.task_id AND t.domain_request_id=p.id)
          OR (t.task_type='weekly_digest_finalize' AND t.domain_request_id=p.id AND EXISTS(
            SELECT 1 FROM stewardship_task_run root WHERE root.id=t.root_id
              AND root.task_type=t.task_type AND root.domain_request_id=p.id
              AND root.idempotency_key=p.id::text))
          OR (t.task_type='outbox_delivery' AND EXISTS(
            SELECT 1 FROM stewardship_outbox_message m
            JOIN stewardship_weekly_digest_recipient r ON r.outbox_id=m.id
            JOIN stewardship_weekly_digest_snapshot s ON s.id=r.snapshot_id
            WHERE m.id=t.domain_request_id AND m.task_id=t.root_id
              AND ((m.state='delivered' AND m.run_id=t.id AND m.task_fence=t.fence AND m.worker_id=t.worker_id)
                OR (m.state='cancelled' AND m.reason='recipient_revoked'
                  AND m.correlation_id=t.id AND m.actor_id=t.worker_id))
              AND s.preparation_id=p.id))))
$$;

CREATE FUNCTION stewardship_weekly_digest_settle_v1() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE p stewardship_weekly_digest_preparation%ROWTYPE;
        occurrence stewardship_schedule_occurrence%ROWTYPE;
        task stewardship_task_run%ROWTYPE;
        proposed jsonb; outcome text; v_reason text;
BEGIN
    PERFORM pg_advisory_xact_lock(736220,1);
    IF TG_TABLE_NAME='stewardship_weekly_digest_preparation' THEN
        SELECT * INTO p FROM stewardship_weekly_digest_preparation WHERE id=NEW.id;
        SELECT * INTO task FROM stewardship_task_run WHERE id=p.run_id;
    ELSIF TG_TABLE_NAME='stewardship_task_run' THEN
        SELECT * INTO p FROM stewardship_weekly_digest_preparation WHERE id=NEW.domain_request_id;
        SELECT * INTO task FROM stewardship_task_run WHERE id=NEW.id;
    ELSE
        SELECT owner.* INTO p FROM stewardship_weekly_digest_recipient r
          JOIN stewardship_weekly_digest_snapshot s ON s.id=r.snapshot_id
          JOIN stewardship_weekly_digest_preparation owner ON owner.id=s.preparation_id
          WHERE r.outbox_id=NEW.id;
        SELECT * INTO task FROM stewardship_task_run WHERE id=CASE
          WHEN NEW.state='cancelled' THEN NEW.correlation_id ELSE NEW.run_id END;
    END IF;
    SELECT disposition,CASE disposition WHEN 'empty' THEN empty_reason ELSE 'weekly_digest_complete' END
      INTO outcome,v_reason FROM stewardship_weekly_digest_completion_ready WHERE preparation_id=p.id;
    IF p.id IS NULL OR outcome IS NULL THEN RETURN NULL; END IF;
    SELECT * INTO occurrence FROM stewardship_schedule_occurrence WHERE id=p.occurrence_id FOR UPDATE;
    IF occurrence.state<>'pending' THEN RETURN NULL; END IF;
    proposed:=to_jsonb(occurrence)||jsonb_build_object('state','running','reason',v_reason,
      'task_id',task.id,'worker_id',task.worker_id,'actor_id',task.worker_id,
      'correlation_id',task.id,'fence',task.fence);
    -- Late truthful provider reconciliation must commit even when its original
    -- claim has expired. A separate metadata finalizer obtains a fresh claim.
    IF NOT stewardship_weekly_digest_completion_v1(proposed) THEN RETURN NULL; END IF;
    UPDATE stewardship_schedule_occurrence SET state='running',reason=v_reason,
      task_id=task.id,worker_id=task.worker_id,actor_id=task.worker_id,
      correlation_id=task.id,fence=task.fence,heartbeat_at=clock_timestamp(),
      lease_expires_at=task.lease_expires_at,attempts=attempts+1,version=version+1
      WHERE id=occurrence.id;
    UPDATE stewardship_schedule_occurrence SET state='succeeded',lease_expires_at=NULL,version=version+1
      WHERE id=occurrence.id;
    INSERT INTO stewardship_schedule_fulfillment(id,definition_id,mode,target,slot,disposition,occurrence_id,actor_id,correlation_id)
      VALUES(gen_random_uuid(),occurrence.definition_id,occurrence.mode,occurrence.target,occurrence.slot,
        outcome,occurrence.id,task.worker_id,task.id);
    RETURN NULL;
END $$;
REVOKE ALL ON FUNCTION stewardship_weekly_digest_settle_v1() FROM PUBLIC;
CREATE CONSTRAINT TRIGGER weekly_cohort_complete AFTER UPDATE ON stewardship_weekly_digest_preparation
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW WHEN (NEW.phase='complete')
EXECUTE FUNCTION stewardship_weekly_digest_settle_v1();
CREATE CONSTRAINT TRIGGER weekly_late_completion AFTER UPDATE ON stewardship_task_run
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW WHEN (NEW.task_type='weekly_digest_finalize' AND NEW.state='running' AND NEW.action='claim')
EXECUTE FUNCTION stewardship_weekly_digest_settle_v1();
CREATE CONSTRAINT TRIGGER weekly_delivery_complete AFTER UPDATE ON stewardship_outbox_message
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW WHEN (NEW.purpose='weekly_digest'
    AND (NEW.state='delivered' OR (NEW.state='cancelled' AND NEW.reason='recipient_revoked')))
EXECUTE FUNCTION stewardship_weekly_digest_settle_v1();

-- From schema/weekly_digests.sql.

CREATE FUNCTION stewardship_weekly_digest_write_admitted_v1(
    relation_name text,proposed jsonb,prior jsonb
) RETURNS boolean LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE p stewardship_weekly_digest_preparation%ROWTYPE;
        o stewardship_schedule_occurrence%ROWTYPE;
        selected stewardship_schedule_occurrence%ROWTYPE;
BEGIN
    IF relation_name NOT IN ('stewardship_schedule_occurrence','stewardship_occurrence_transition',
        'stewardship_outbox_message','stewardship_outbox_render','stewardship_outbox_event')
    THEN RETURN false; END IF;
    IF NOT EXISTS(SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
        AND mode='ExclusiveLock' AND granted) THEN RETURN false; END IF;
    IF relation_name='stewardship_occurrence_transition' THEN
        SELECT * INTO o FROM stewardship_schedule_occurrence WHERE id=(proposed->>'occurrence_id')::uuid;
        RETURN prior IS NULL AND o.id IS NOT NULL
          AND (proposed->>'version')::bigint=o.version AND proposed->>'after_state'=o.state
          AND (proposed->>'actor_id')::uuid=o.actor_id
          AND (proposed->>'correlation_id')::uuid=o.correlation_id
          AND stewardship_weekly_digest_write_admitted_v1('stewardship_schedule_occurrence',to_jsonb(o),
              CASE WHEN o.state='pending' THEN NULL ELSE to_jsonb(o)||jsonb_build_object('state','pending') END);
    END IF;
    SELECT preparation.* INTO p FROM stewardship_weekly_digest_preparation preparation
      JOIN stewardship_task_run t ON t.root_id=preparation.task_id
      JOIN stewardship_task_event e ON e.run_id=t.id
      WHERE e.id=(proposed->>'correlation_id')::uuid AND e.action='claim' AND e.state='running'
        AND e.worker_id=(proposed->>'actor_id')::uuid AND e.fence=t.fence
        AND stewardship_weekly_digest_live_v1(preparation.id,t.id,e.fence,e.worker_id);
    IF p.id IS NULL THEN RETURN false; END IF;
    IF relation_name IN ('stewardship_outbox_message','stewardship_outbox_render','stewardship_outbox_event') THEN
        RETURN stewardship_weekly_digest_mail_write_v1(p.id,relation_name,proposed,prior);
    END IF;
    IF proposed->>'target'<>'admins'
      OR (proposed->>'definition_id')::uuid<>p.definition_id
      OR (proposed->>'revision_id')::uuid<>p.revision_id OR proposed->>'mode'<>p.mode
      OR (proposed->>'due_at')::timestamptz>p.cutoff
      OR proposed->>'task_id' IS NOT NULL OR proposed->>'outbox_id' IS NOT NULL
    THEN RETURN false; END IF;
    IF prior IS NULL THEN
        IF p.phase='cover' AND proposed->>'slot'='recovery:'||p.id::text THEN
            RETURN proposed->>'state'='pending' AND p.occurrence_id IS NULL
                AND (proposed->>'due_at')::timestamptz=(SELECT max(candidate.due_at)
                    FROM stewardship_schedule_occurrence candidate WHERE candidate.id IN (
                        SELECT stewardship_weekly_digest_predecessors_v1(p.definition_id,p.mode,p.rehearsal_epoch_id)))
                AND NOT EXISTS(SELECT 1 FROM stewardship_schedule_occurrence candidate
                    WHERE candidate.revision_id=p.revision_id AND candidate.mode=p.mode AND candidate.target='admins'
                      AND candidate.slot='recovery:'||p.id::text
                      AND candidate.id<>(proposed->>'id')::uuid);
        END IF;
        RETURN p.phase='dates' AND proposed->>'state'='pending'
          AND stewardship_catchup_due_v1(p.definition_id,proposed->>'slot') IS NOT NULL
          AND (proposed->>'due_at')::timestamptz=stewardship_catchup_due_v1(p.definition_id,proposed->>'slot');
    END IF;
    IF p.phase<>'cover' OR prior->>'state'<>'pending' OR proposed->>'state'<>'coalesced'
      OR proposed->>'reason'<>'missed_weekly_recovery'
      OR prior->>'task_id' IS NOT NULL OR prior->>'outbox_id' IS NOT NULL
      OR EXISTS(SELECT 1 FROM stewardship_weekly_digest_preparation other
          WHERE other.id<>p.id AND other.occurrence_id=(prior->>'id')::uuid)
    THEN RETURN false; END IF;
    SELECT * INTO selected FROM stewardship_schedule_occurrence WHERE id=(proposed->>'replacement_id')::uuid;
    RETURN selected.id IS NOT NULL AND selected.state='pending'
      AND selected.definition_id=p.definition_id AND selected.revision_id=p.revision_id
      AND selected.mode=p.mode AND selected.target='admins'
      AND selected.task_id IS NULL AND selected.outbox_id IS NULL
      AND selected.due_at BETWEEN (proposed->>'due_at')::timestamptz AND p.cutoff
      AND NOT stewardship_schedule_slot_excluded_v1(selected.definition_id,selected.mode,selected.target,selected.slot)
      AND NOT EXISTS(SELECT 1 FROM stewardship_weekly_digest_preparation other
          WHERE other.id<>p.id AND other.occurrence_id=selected.id);
END $$;

-- From schema/weekly_dispatch.sql.

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

-- From schema/weekly_fanout.sql.

CREATE FUNCTION stewardship_weekly_digest_mail_write_v1(
    preparation uuid,relation_name text,proposed jsonb,prior jsonb
) RETURNS boolean LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE p stewardship_weekly_digest_preparation%ROWTYPE;
        s stewardship_weekly_digest_snapshot%ROWTYPE;
        m stewardship_outbox_message%ROWTYPE;
        runtime stewardship_system_configuration%ROWTYPE;
        template uuid; email jsonb;
BEGIN
    -- The outer predicate establishes the current exact claim and work lock.
    -- This admits only allocation. Provider dispatch needs its separate owner.
    SELECT * INTO p FROM stewardship_weekly_digest_preparation WHERE id=preparation;
    SELECT * INTO s FROM stewardship_weekly_digest_snapshot WHERE preparation_id=p.id;
    IF prior IS NOT NULL OR p.phase<>'fanout' OR s.id IS NULL
      OR (s.information='[]'::jsonb AND s.corrections='[]'::jsonb) THEN RETURN false; END IF;
    IF relation_name='stewardship_outbox_message' THEN
        RETURN proposed->>'purpose'='weekly_digest' AND proposed->>'mode'=p.mode
          AND (proposed->>'scope_id')::uuid=p.campaign_id
          AND (proposed->>'campaign_id')::uuid=p.campaign_id
          AND proposed->>'family_id' IS NULL AND proposed->>'credential_namespace'='none'
          AND proposed->>'state'='pending' AND proposed->>'action'='created'
          AND (proposed->>'version')::bigint=1;
    END IF;
    SELECT * INTO m FROM stewardship_outbox_message WHERE id=(proposed->>'message_id')::uuid;
    IF m.id IS NULL OR m.purpose<>'weekly_digest' OR m.campaign_id<>p.campaign_id
      OR m.mode<>p.mode OR m.state<>'pending' OR m.version<>1
      OR m.correlation_id IS DISTINCT FROM (proposed->>'correlation_id')::uuid
      OR m.actor_id IS DISTINCT FROM (proposed->>'actor_id')::uuid THEN RETURN false; END IF;
    IF relation_name='stewardship_outbox_event' THEN
        RETURN proposed->>'action'='created' AND proposed->>'state'='pending'
          AND (proposed->>'version')::bigint=1;
    END IF;
    IF relation_name<>'stewardship_outbox_render' THEN RETURN false; END IF;
    SELECT * INTO runtime FROM stewardship_system_configuration;
    SELECT content.id INTO template FROM stewardship_content_version content
      JOIN stewardship_schedule_revision revision ON revision.id=p.revision_id
      WHERE content.configuration_id=runtime.active_configuration_id
        AND content.campaign_id=p.campaign_id AND content.kind='email'
        AND content.record_id=(revision.values->>'template_version')::uuid;
    SELECT settings INTO email FROM stewardship_applied_integration
      WHERE configuration_id=runtime.active_configuration_id AND kind='email';
    RETURN (proposed->>'id')::uuid=m.render_id
      AND (proposed->>'configuration_id')::uuid=runtime.active_configuration_id
      AND (proposed->>'template_id')::uuid=template
      AND proposed->>'sender'=email->>'sender' AND proposed->>'reply_to'=email->>'reply_to'
      AND jsonb_array_length(proposed->'intended_recipients')=1
      AND s.recipients ? (proposed->'intended_recipients'->>0)
      AND proposed->'routed_recipients'=CASE p.mode WHEN 'testing'
        THEN jsonb_build_array(runtime.testing_recipient) ELSE proposed->'intended_recipients' END;
END $$;

CREATE FUNCTION stewardship_weekly_mail_commit_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF NEW.purpose='weekly_digest' AND EXISTS(
        SELECT 1 FROM stewardship_task_event e JOIN stewardship_task_run t ON t.id=e.run_id
        WHERE e.id=NEW.correlation_id AND t.task_type='weekly_digest_prepare')
      AND NOT EXISTS(SELECT 1 FROM stewardship_weekly_digest_recipient
        WHERE id=NEW.semantic_key AND outbox_id=NEW.id
          AND actor_id=NEW.actor_id AND correlation_id=NEW.correlation_id) THEN
        RAISE EXCEPTION 'Weekly message requires atomic recipient ownership' USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END $$;
REVOKE ALL ON FUNCTION stewardship_weekly_mail_commit_v1() FROM PUBLIC;
CREATE CONSTRAINT TRIGGER weekly_mail_complete AFTER INSERT ON stewardship_outbox_message
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION stewardship_weekly_mail_commit_v1();

-- From schema/weekly_manual.sql.
CREATE FUNCTION stewardship_weekly_manual_allocate_v1() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE c stewardship_campaign%ROWTYPE; d stewardship_schedule_definition%ROWTYPE;
    runtime stewardship_system_configuration%ROWTYPE; epoch uuid; cycle bigint;
    instant timestamptz:=stewardship_campaign_now_v1(); slot text:='manual:'||NEW.id::text;
BEGIN
    SELECT * INTO c FROM stewardship_campaign WHERE id=NEW.campaign_id;
    SELECT * INTO runtime FROM stewardship_system_configuration;
    SELECT * INTO d FROM stewardship_schedule_definition
        WHERE campaign_id=c.id AND kind='weekly_digest' AND current_revision_id IS NOT NULL;
    IF runtime.mode='testing' THEN
        SELECT rehearsal_epoch_id INTO epoch FROM stewardship_campaign_credentials WHERE campaign_id=c.id;
    END IF;
    cycle:=CASE WHEN runtime.mode='production' THEN c.production_cycle ELSE 0 END;
    INSERT INTO stewardship_schedule_occurrence(
        id,actor_id,correlation_id,version,definition_id,revision_id,mode,routing,
        target,slot,due_at,occurrence_key,state,fence,attempts,reason,pause_version,production_cycle)
    VALUES(NEW.id,NEW.actor_id,NEW.id,1,d.id,d.current_revision_id,runtime.mode,
        CASE runtime.mode WHEN 'testing' THEN 'testing_override' ELSE 'production' END,
        'admins',slot,instant,encode(sha256(convert_to(
            '["'||d.current_revision_id::text||'","'||runtime.mode||'","admins","'||slot||'"'
            ||CASE WHEN cycle>0 THEN ',["production_cycle",'||cycle::text||']' ELSE '' END||']','UTF8')),'hex'),
        'pending',0,0,'',CASE WHEN runtime.mode='production' AND c.delivery_paused THEN c.pause_version ELSE NULL END,cycle);
    INSERT INTO stewardship_weekly_digest_preparation(
        id,actor_id,correlation_id,version,campaign_id,definition_id,revision_id,
        campaign_configuration_id,task_id,mode,rehearsal_epoch_id,cutoff,phase,occurrence_id)
    VALUES(NEW.id,NEW.actor_id,NEW.id,1,c.id,d.id,d.current_revision_id,c.active_configuration_id,
        NEW.task_id,runtime.mode,epoch,instant,'capture',NEW.id);
    INSERT INTO stewardship_audit_event(id,actor_id,correlation_id,event_type,subject_id,campaign_reference)
    VALUES(gen_random_uuid(),NEW.actor_id,NEW.id,'weekly_manual_requested',NEW.id,c.id);
    RETURN NULL;
END $$;
REVOKE ALL ON FUNCTION stewardship_weekly_manual_allocate_v1() FROM PUBLIC;
CREATE TRIGGER weekly_manual_allocate AFTER INSERT ON stewardship_weekly_manual_request
FOR EACH ROW EXECUTE FUNCTION stewardship_weekly_manual_allocate_v1();
