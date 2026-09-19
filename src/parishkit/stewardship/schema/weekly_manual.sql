-- Explicit Admin intent is separate from provider retry and normal weekly slots.
CREATE TABLE "stewardship_weekly_manual_request" ("id" uuid NOT NULL PRIMARY KEY, "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "actor_id" uuid NULL, "correlation_id" uuid NOT NULL, "campaign_id" uuid NOT NULL, "configuration_id" uuid NOT NULL, "task_id" uuid NOT NULL UNIQUE);
ALTER TABLE "stewardship_weekly_manual_request" ADD CONSTRAINT "stewardship_weekly_m_campaign_id_ff6fabed_fk_stewardsh" FOREIGN KEY ("campaign_id") REFERENCES "stewardship_campaign" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_weekly_manual_request" ADD CONSTRAINT "stewardship_weekly_m_configuration_id_ced0d984_fk_stewardsh" FOREIGN KEY ("configuration_id") REFERENCES "stewardship_configuration_version" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_weekly_manual_request" ADD CONSTRAINT "stewardship_weekly_m_task_id_9ccec600_fk_stewardsh" FOREIGN KEY ("task_id") REFERENCES "stewardship_task_run" ("id") DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX "stewardship_weekly_manual_request_correlation_id_b0404157" ON "stewardship_weekly_manual_request" ("correlation_id");
CREATE INDEX "stewardship_weekly_manual_request_campaign_id_ff6fabed" ON "stewardship_weekly_manual_request" ("campaign_id");
CREATE INDEX "stewardship_weekly_manual_request_configuration_id_ced0d984" ON "stewardship_weekly_manual_request" ("configuration_id");

CREATE FUNCTION stewardship_weekly_manual_guard_v1() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE c stewardship_campaign%ROWTYPE; d stewardship_schedule_definition%ROWTYPE;
    runtime stewardship_system_configuration%ROWTYPE; epoch uuid;
BEGIN
    PERFORM pg_advisory_xact_lock(736220,1);
    IF TG_OP<>'INSERT' THEN
        RAISE EXCEPTION 'Manual weekly request history is immutable' USING ERRCODE='23514';
    END IF;
    SELECT * INTO c FROM stewardship_campaign WHERE id=NEW.campaign_id FOR UPDATE;
    SELECT * INTO runtime FROM stewardship_system_configuration;
    SELECT * INTO d FROM stewardship_schedule_definition
        WHERE campaign_id=c.id AND kind='weekly_digest' AND current_revision_id IS NOT NULL;
    IF runtime.mode='testing' THEN
        SELECT rehearsal_epoch_id INTO epoch FROM stewardship_campaign_credentials WHERE campaign_id=c.id;
    END IF;
    IF session_user<>'pk_stewardship_web' OR NEW.actor_id IS NULL
      OR stewardship_export_authorized_v1(NEW.actor_id,true) IS NOT TRUE
      OR NEW.correlation_id<>NEW.id OR c.id IS NULL OR d.id IS NULL
      OR NEW.configuration_id IS DISTINCT FROM runtime.active_configuration_id
      OR NOT stewardship_weekly_digest_scope_v1(c.id,d.current_revision_id,c.active_configuration_id,runtime.mode,epoch)
      OR stewardship_weekly_digest_unresolved_v1(d.id,runtime.mode,epoch)
      OR NOT EXISTS(SELECT 1 FROM stewardship_task_run t WHERE t.id=NEW.task_id AND t.root_id=t.id
          AND t.task_type='weekly_digest_prepare' AND t.domain_request_id=NEW.id
          AND t.idempotency_key=NEW.id::text AND t.state='queued'
          AND t.initiated_by_id=NEW.actor_id AND t.correlation_id=NEW.id)
    THEN RAISE EXCEPTION 'Manual weekly request requires current Admin scope and resolved prior work'
        USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION stewardship_weekly_manual_guard_v1() FROM PUBLIC;
CREATE TRIGGER weekly_manual_guard BEFORE INSERT OR UPDATE OR DELETE ON stewardship_weekly_manual_request
FOR EACH ROW EXECUTE FUNCTION stewardship_weekly_manual_guard_v1();

-- Runtime Web may insert only the intent above. It has no direct occurrence or
-- preparation INSERT grant. The closed command derives every subordinate field.
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

CREATE FUNCTION stewardship_weekly_preparation_history_v1(preparation uuid)
RETURNS jsonb LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT stewardship_weekly_history_v1(p.campaign_id,p.mode,p.rehearsal_epoch_id)
      ||CASE WHEN EXISTS(SELECT 1 FROM stewardship_weekly_manual_request WHERE id=p.id)
          THEN jsonb_build_object('watermark',0,'corrected','[]'::jsonb) ELSE '{}'::jsonb END
    FROM stewardship_weekly_digest_preparation p WHERE p.id=$1
$$;
