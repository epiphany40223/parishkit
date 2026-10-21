-- Accepted coverage is per original Admin and exact item/disposition, never a
-- successful flag on a whole report or an inference from a queued message.
CREATE FUNCTION stewardship_weekly_prior_items_v1(snapshot uuid,address text)
RETURNS jsonb LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    WITH desired AS MATERIALIZED (
        SELECT s.id,s.information,s.corrections,p.campaign_id,p.mode,p.rehearsal_epoch_id,
            EXISTS(SELECT 1 FROM stewardship_weekly_manual_request WHERE id=p.id) AS manual
        FROM stewardship_weekly_digest_snapshot s
        JOIN stewardship_weekly_digest_preparation p ON p.id=s.preparation_id
        WHERE s.id=$1 AND s.recipients ? $2
    ), accepted AS MATERIALIZED (
        SELECT m.id,r.information,r.corrections FROM desired d
        JOIN stewardship_weekly_digest_preparation p ON p.campaign_id=d.campaign_id
          AND p.mode=d.mode AND p.rehearsal_epoch_id IS NOT DISTINCT FROM d.rehearsal_epoch_id
        JOIN stewardship_weekly_digest_snapshot s ON s.preparation_id=p.id AND s.id<>d.id
        JOIN stewardship_weekly_digest_recipient r ON r.snapshot_id=s.id AND r.address=$2
        JOIN stewardship_outbox_message m ON m.id=r.outbox_id AND m.semantic_key=r.id
          AND m.state='delivered' AND m.purpose='weekly_digest'
          AND m.campaign_id=d.campaign_id AND m.mode=d.mode
        WHERE NOT d.manual AND NOT EXISTS(SELECT 1 FROM stewardship_weekly_manual_request WHERE id=p.id)
          AND (EXISTS(SELECT 1 FROM jsonb_array_elements(d.information) i WHERE r.information @> jsonb_build_array(i))
           OR EXISTS(SELECT 1 FROM jsonb_array_elements(d.corrections) i WHERE r.corrections @> jsonb_build_array(i)))
    ) SELECT jsonb_build_object(
        'information',coalesce((SELECT jsonb_agg(i ORDER BY n)
            FROM jsonb_array_elements(d.information) WITH ORDINALITY AS wanted(i,n)
            WHERE NOT EXISTS(SELECT 1 FROM accepted a WHERE a.information @> jsonb_build_array(i))),'[]'::jsonb),
        'corrections',coalesce((SELECT jsonb_agg(i ORDER BY n)
            FROM jsonb_array_elements(d.corrections) WITH ORDINALITY AS wanted(i,n)
            WHERE NOT EXISTS(SELECT 1 FROM accepted a WHERE a.corrections @> jsonb_build_array(i))),'[]'::jsonb),
        'covered_messages',coalesce((SELECT jsonb_agg(id::text ORDER BY id::text) FROM accepted),'[]'::jsonb)
    ) FROM desired d
$$;

CREATE FUNCTION stewardship_weekly_recipient_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE expected jsonb;
BEGIN
    PERFORM pg_advisory_xact_lock(736220,1);
    expected:=stewardship_weekly_prior_items_v1(NEW.snapshot_id,NEW.address);
    IF expected IS NULL OR NEW.information IS DISTINCT FROM expected->'information'
      OR NEW.corrections IS DISTINCT FROM expected->'corrections'
      OR NEW.covered_messages IS DISTINCT FROM expected->'covered_messages'
      OR NOT EXISTS(SELECT 1 FROM stewardship_weekly_digest_snapshot s
        JOIN stewardship_weekly_digest_preparation p ON p.id=s.preparation_id
        JOIN stewardship_task_run t ON t.root_id=p.task_id
        JOIN stewardship_task_event e ON e.run_id=t.id AND e.action='claim'
          AND e.fence=t.fence AND e.worker_id=t.worker_id AND e.state='running'
        WHERE s.id=NEW.snapshot_id AND p.phase='fanout' AND s.recipients ? NEW.address
          AND (s.information<>'[]'::jsonb OR s.corrections<>'[]'::jsonb)
          AND NEW.actor_id=t.worker_id AND NEW.correlation_id=e.id
          AND stewardship_weekly_digest_live_v1(p.id,t.id,t.fence,t.worker_id)) THEN
        RAISE EXCEPTION 'Weekly recipient requires exact owned item coverage' USING ERRCODE='23514';
    END IF;
    IF NEW.information='[]'::jsonb AND NEW.corrections='[]'::jsonb THEN
        IF NEW.outbox_id IS NOT NULL OR NEW.covered_messages='[]'::jsonb
          OR NEW.subject<>'' OR NEW.html<>'' OR NEW.text<>'' THEN
            RAISE EXCEPTION 'Weekly covered recipient cannot queue another message' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.outbox_id IS NULL OR btrim(NEW.subject)='' OR NEW.subject ~ E'[\r\n]'
      OR octet_length(NEW.html) NOT BETWEEN 1 AND 8388608
      OR octet_length(NEW.text) NOT BETWEEN 1 AND 8388608
      OR NOT EXISTS(SELECT 1 FROM stewardship_weekly_digest_snapshot s
        JOIN stewardship_weekly_digest_preparation p ON p.id=s.preparation_id
        JOIN stewardship_outbox_message m ON m.id=NEW.outbox_id
        JOIN stewardship_outbox_render content ON content.id=m.render_id
        WHERE s.id=NEW.snapshot_id AND m.semantic_key=NEW.id
          AND m.scope_id=p.campaign_id AND m.campaign_id=p.campaign_id
          AND m.family_id IS NULL AND m.purpose='weekly_digest' AND m.mode=p.mode
          AND m.credential_namespace='none' AND m.state='pending' AND m.version=1
          AND m.correlation_id=NEW.correlation_id AND m.actor_id=NEW.actor_id
          AND content.intended_recipients=jsonb_build_array(NEW.address)
          AND right(content.html,length(NEW.html))=NEW.html
          AND right(content.text,length(NEW.text))=NEW.text) THEN
        RAISE EXCEPTION 'Weekly recipient requires its own exact compiled message' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER weekly_recipient_insert BEFORE INSERT ON stewardship_weekly_digest_recipient
FOR EACH ROW EXECUTE FUNCTION stewardship_weekly_recipient_guard_v1();
