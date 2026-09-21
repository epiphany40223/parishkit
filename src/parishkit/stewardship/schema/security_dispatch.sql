-- A security alert is owned by its recorded recipient list, never by current
-- Administrator grants: the recipients are the Administrators who existed
-- before the expansion, and each is told even if revoked since.
CREATE FUNCTION stewardship_security_dispatch_live_v1(message uuid)
RETURNS boolean LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT EXISTS(SELECT 1 FROM stewardship_outbox_message m
      JOIN stewardship_security_recipient recipient ON recipient.outbox_id=m.id AND recipient.id=m.semantic_key
      JOIN stewardship_security_cohort c ON c.id=recipient.cohort_id
      WHERE m.id=$1 AND m.purpose='security_event' AND m.routing='operational'
        AND m.scope_id=c.parish_id AND m.mode=c.mode AND m.family_id IS NULL
        AND m.campaign_id IS NULL AND m.credential_namespace='none'
        AND c.addresses ? recipient.address
        AND (SELECT count(*) FROM stewardship_security_recipient WHERE cohort_id=c.id)=c.recipient_count)
$$;

CREATE FUNCTION stewardship_security_dispatch_render_v1(proposed jsonb, message uuid)
RETURNS boolean LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT EXISTS(SELECT 1 FROM stewardship_outbox_message m
      JOIN stewardship_security_recipient recipient ON recipient.outbox_id=m.id AND recipient.id=m.semantic_key
      JOIN stewardship_security_cohort c ON c.id=recipient.cohort_id
      CROSS JOIN stewardship_system_configuration runtime
      WHERE m.id=$2 AND m.purpose='security_event' AND (proposed->>'message_id')::uuid=m.id
        AND stewardship_security_render_matches_v1(proposed,c.event_id,runtime.active_configuration_id,
          recipient.address,runtime.mode))
$$;

CREATE FUNCTION stewardship_security_dispatch_write_v1(relation_name text,proposed jsonb,prior jsonb)
RETURNS boolean LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE task stewardship_task_run%ROWTYPE; message stewardship_outbox_message%ROWTYPE;
BEGIN
    IF relation_name NOT IN ('stewardship_outbox_message','stewardship_outbox_render','stewardship_outbox_event')
      OR NOT EXISTS(SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
        AND mode='ExclusiveLock' AND granted) THEN RETURN false; END IF;
    SELECT * INTO task FROM stewardship_task_run WHERE id=(proposed->>'correlation_id')::uuid;
    SELECT * INTO message FROM stewardship_outbox_message
      WHERE id=task.domain_request_id AND task_id=task.root_id AND purpose='security_event';
    IF task.id IS NULL OR task.task_type<>'outbox_delivery' OR message.id IS NULL
      OR NOT EXISTS(SELECT 1 FROM stewardship_security_recipient recipient
        JOIN stewardship_security_cohort c ON c.id=recipient.cohort_id
        WHERE recipient.outbox_id=message.id AND recipient.id=message.semantic_key
          AND message.scope_id=c.parish_id AND message.mode=c.mode)
      OR (CASE WHEN relation_name='stewardship_outbox_message' THEN proposed->>'id'
        ELSE proposed->>'message_id' END)::uuid IS DISTINCT FROM message.id THEN RETURN false; END IF;
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
        RETURN stewardship_security_dispatch_live_v1(message.id) AND EXISTS(
          SELECT 1 FROM stewardship_outbox_render r WHERE r.id=(proposed->>'render_id')::uuid
            AND stewardship_security_dispatch_render_v1(to_jsonb(r),message.id));
      END IF;
      IF proposed->>'action' IN ('accept','retry_unaccepted','fail_unaccepted','mark_unknown') THEN
        RETURN prior->>'state'='submitting' AND (prior->>'run_id')::uuid=task.id
          AND (prior->>'task_fence')::bigint=task.fence AND (prior->>'worker_id')::uuid=task.worker_id
          AND proposed->>'reason' LIKE 'smtp_%';
      END IF;
      -- A recorded recipient is never revoked; only a failed preparation cancels.
      IF proposed->>'action'='cancel_unsent' AND proposed->>'reason'='preparation_failed' THEN
        RETURN EXISTS(SELECT 1 FROM stewardship_security_recipient recipient
          JOIN stewardship_security_cohort c ON c.id=recipient.cohort_id
          JOIN stewardship_task_run original ON original.id=c.run_id
          WHERE recipient.outbox_id=message.id
            AND (SELECT count(*) FROM stewardship_security_recipient WHERE cohort_id=c.id)<c.recipient_count
            AND (SELECT state FROM stewardship_task_run WHERE root_id=original.root_id
              ORDER BY retry_sequence DESC LIMIT 1) IN ('failed','cancelled'));
      END IF;
      RETURN false;
    END IF;
    IF relation_name='stewardship_outbox_render' THEN
      RETURN prior IS NULL AND message.state IN ('pending','retry_wait')
        AND stewardship_security_dispatch_live_v1(message.id)
        AND stewardship_security_dispatch_render_v1(proposed,message.id);
    END IF;
    RETURN prior IS NULL AND proposed->>'action'=message.action
      AND (proposed->>'version')::bigint=message.version;
END $$;

-- A failed security alert delivery or preparation leaves the same fixed safe
-- ERROR the operational owner leaves, never another CRITICAL alert.
CREATE FUNCTION stewardship_security_delivery_error_v1()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE failed boolean:=false;
BEGIN
    IF TG_TABLE_NAME='stewardship_outbox_event' THEN
      IF NOT ((NEW.state IN ('retry_wait','permanent_failure') AND NEW.reason LIKE 'smtp_%')
        OR (NEW.state='cancelled' AND NEW.reason='preparation_failed'))
        THEN RETURN NULL; END IF;
      failed:=EXISTS(SELECT 1 FROM stewardship_outbox_message
        WHERE id=NEW.message_id AND purpose='security_event');
    ELSIF TG_TABLE_NAME='stewardship_task_event' THEN
      IF NEW.action NOT IN ('permanent_failure','recovery_fail') THEN RETURN NULL; END IF;
      failed:=EXISTS(SELECT 1 FROM stewardship_task_run WHERE id=NEW.run_id
          AND task_type='security_prepare') OR EXISTS(SELECT 1 FROM stewardship_task_run t
          JOIN stewardship_outbox_message m ON m.id=t.domain_request_id AND m.task_id=t.root_id
          WHERE t.id=NEW.run_id AND t.task_type='outbox_delivery' AND m.purpose='security_event'
            AND m.state IN ('pending','retry_wait'));
    END IF;
    IF failed THEN
      INSERT INTO stewardship_operational_log(id,actor_id,correlation_id,event,level,schema,context)
      VALUES(gen_random_uuid(),NEW.actor_id,NEW.correlation_id,'task_failed','ERROR','exception','{}'::jsonb);
    END IF;
    RETURN NULL;
END $$;
CREATE TRIGGER stewardship_security_delivery_error AFTER INSERT ON stewardship_outbox_event
FOR EACH ROW EXECUTE FUNCTION stewardship_security_delivery_error_v1();
CREATE TRIGGER stewardship_security_preparation_error AFTER INSERT ON stewardship_task_event
FOR EACH ROW EXECUTE FUNCTION stewardship_security_delivery_error_v1();
REVOKE ALL ON FUNCTION stewardship_security_delivery_error_v1() FROM PUBLIC;
