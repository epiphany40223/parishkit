-- A successful interval requires its entire original cohort, not one accepted
-- child. Empty captures advance the watermark without inventing provider mail.
CREATE VIEW stewardship_weekly_digest_completion_ready AS
    SELECT p.id AS preparation_id,
      CASE WHEN EXISTS(SELECT 1 FROM stewardship_weekly_digest_recipient r
        LEFT JOIN stewardship_outbox_message m ON m.id=r.outbox_id
        WHERE r.snapshot_id=s.id AND (m.state='delivered'
          OR (r.outbox_id IS NULL AND jsonb_array_length(r.covered_messages)>0)))
        THEN 'delivered' ELSE 'empty' END AS disposition,
      CASE WHEN s.information='[]'::jsonb AND s.corrections='[]'::jsonb
        THEN 'weekly_digest_empty' ELSE 'weekly_digest_no_current_recipients' END AS empty_reason
    FROM stewardship_weekly_digest_preparation p
    JOIN stewardship_weekly_digest_snapshot s ON s.preparation_id=p.id
    WHERE p.phase='complete' AND (
      (s.information='[]'::jsonb AND s.corrections='[]'::jsonb)
      OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements_text(s.recipients) wanted(address)
        WHERE NOT EXISTS(SELECT 1 FROM stewardship_weekly_digest_recipient r
          LEFT JOIN stewardship_outbox_message m ON m.id=r.outbox_id
          WHERE r.snapshot_id=s.id AND r.address=wanted.address
            AND (m.state='delivered' OR (m.state='cancelled' AND m.reason='recipient_revoked')
              OR (r.outbox_id IS NULL AND r.information='[]'::jsonb AND r.corrections='[]'::jsonb
                AND jsonb_array_length(r.covered_messages)>0))
            AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements_text(r.covered_messages) proof(message_id)
              WHERE NOT EXISTS(SELECT 1 FROM stewardship_outbox_message accepted
                WHERE accepted.id=proof.message_id::uuid AND accepted.state='delivered')))));
REVOKE ALL ON stewardship_weekly_digest_completion_ready FROM PUBLIC;

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
