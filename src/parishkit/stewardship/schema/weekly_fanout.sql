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
