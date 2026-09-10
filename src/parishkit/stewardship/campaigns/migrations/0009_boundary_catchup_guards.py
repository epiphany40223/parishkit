"""Atomic boundary outcomes and fenced, append-only catch-up progress."""

# ruff: noqa: E501
from django.db import migrations

SQL = """
CREATE FUNCTION stewardship_boundary_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE c stewardship_campaign%ROWTYPE; r stewardship_system_configuration%ROWTYPE;
    p stewardship_campaign_configuration%ROWTYPE; due timestamptz;
BEGIN
    PERFORM pg_advisory_xact_lock(736220,1);
    IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Boundary history cannot be deleted' USING ERRCODE='23514'; END IF;
    SELECT * INTO r FROM stewardship_system_configuration FOR UPDATE;
    SELECT * INTO c FROM stewardship_campaign WHERE id=NEW.campaign_id FOR UPDATE;
    SELECT * INTO p FROM stewardship_campaign_configuration WHERE id=c.active_configuration_id;
    due:=CASE WHEN NEW.kind='start' THEN p.starts_at ELSE p.ends_at END;
    IF TG_OP='INSERT' THEN
        IF NEW.state<>'pending' OR NEW.version<>1 OR NEW.due_at<>due
           OR NEW.task_id IS NOT NULL OR NEW.task_fence IS NOT NULL OR NEW.reason<>''
           OR c.id IS DISTINCT FROM r.current_campaign_id OR r.restore_review_required
           OR EXISTS(SELECT 1 FROM stewardship_campaign_work_gate WHERE campaign_id=c.id AND state IN ('preparing','running','tombstone')) THEN
            RAISE EXCEPTION 'Invalid boundary allocation' USING ERRCODE='23514'; END IF;
    ELSE
        IF OLD.state<>'pending' THEN RAISE EXCEPTION 'Boundary result is immutable' USING ERRCODE='23514'; END IF;
        IF NEW.state='succeeded' AND NOT EXISTS(
            SELECT 1 FROM stewardship_campaign_transition t WHERE t.id=NEW.transition_id
            AND t.boundary_id=NEW.id AND t.campaign_id=NEW.campaign_id AND t.action=NEW.kind
            AND t.actor_id IS NOT DISTINCT FROM NEW.actor_id AND t.correlation_id=NEW.correlation_id
        ) THEN RAISE EXCEPTION 'Boundary success requires exact transition' USING ERRCODE='23514'; END IF;
        IF NEW.state='skipped' AND (NEW.reason NOT IN ('not_applicable','boundary_replaced') OR (
            NEW.due_at=due AND c.id=r.current_campaign_id AND r.mode='production'
            AND ((NEW.kind='start' AND c.state='scheduled') OR (NEW.kind='close' AND c.state IN ('scheduled','active')))
        )) THEN RAISE EXCEPTION 'Applicable boundary cannot be skipped' USING ERRCODE='23514'; END IF;
        IF NEW.state='skipped' AND NEW.reason='not_applicable' AND NOT EXISTS(
            SELECT 1 FROM stewardship_task_run t WHERE t.id=NEW.task_id AND t.task_type='campaign_boundary'
            AND t.domain_request_id=c.id AND t.state='running' AND t.worker_id=NEW.actor_id
            AND t.fence=NEW.task_fence AND t.lease_expires_at>clock_timestamp()
        ) THEN RAISE EXCEPTION 'Boundary skip requires current fenced worker' USING ERRCODE='23514'; END IF;
        IF (NEW.task_id,NEW.task_fence) IS DISTINCT FROM (OLD.task_id,OLD.task_fence) THEN
            IF NEW.state<>'pending' OR NOT EXISTS(
                SELECT 1 FROM stewardship_task_run t WHERE t.id=NEW.task_id AND t.task_type='campaign_boundary'
                AND t.domain_request_id=c.id AND t.state='running' AND t.worker_id=NEW.actor_id
                AND t.fence=NEW.task_fence AND t.lease_expires_at>clock_timestamp()
            ) OR EXISTS(SELECT 1 FROM stewardship_task_run WHERE id=OLD.task_id AND fence=OLD.task_fence AND state='running' AND lease_expires_at>clock_timestamp()) THEN
                RAISE EXCEPTION 'Boundary task binding requires current worker ownership' USING ERRCODE='23514'; END IF;
        END IF;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER stewardship_boundary_guard_v1 BEFORE INSERT OR UPDATE OR DELETE ON stewardship_campaign_boundary
FOR EACH ROW EXECUTE FUNCTION stewardship_boundary_guard_v1();

CREATE FUNCTION stewardship_boundary_audit_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
    IF NEW.state='skipped' THEN
        INSERT INTO stewardship_audit_event(id,actor_id,correlation_id,event_type,subject_id,campaign_reference)
        VALUES(gen_random_uuid(),NEW.actor_id,NEW.correlation_id,'campaign_boundary_skipped',NEW.id,NEW.campaign_id);
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER stewardship_boundary_audit_v1 AFTER UPDATE ON stewardship_campaign_boundary
FOR EACH ROW EXECUTE FUNCTION stewardship_boundary_audit_v1();

CREATE FUNCTION stewardship_catchup_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
    IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Catch-up history cannot be deleted' USING ERRCODE='23514'; END IF;
    IF TG_OP='INSERT' THEN
        IF NEW.version<>1 OR NEW.phase<>'pending' OR NEW.cursor<>'' OR NEW.groups_completed<>0 OR NEW.items_completed<>0
           OR NEW.completed_at IS NOT NULL OR NEW.failure_code<>'' OR NEW.task_root_id IS NOT NULL OR NEW.source_snapshot_id IS NOT NULL
           OR NOT EXISTS(SELECT 1 FROM stewardship_campaign_transition t WHERE t.id=NEW.activation_id
               AND t.campaign_id=NEW.campaign_id AND t.configuration_id=NEW.configuration_id AND t.action='activate'
               AND t.actor_id IS NOT DISTINCT FROM NEW.actor_id AND t.correlation_id=NEW.correlation_id)
        THEN RAISE EXCEPTION 'Catch-up demand requires activation evidence' USING ERRCODE='23514'; END IF;
    ELSE
        IF OLD.completed_at IS NOT NULL THEN RAISE EXCEPTION 'Completed catch-up is immutable' USING ERRCODE='23514'; END IF;
        IF OLD.task_root_id IS NULL AND NEW.task_root_id IS NOT NULL THEN
            IF NEW.source_snapshot_id IS NULL OR NOT EXISTS(SELECT 1 FROM stewardship_task_run t
                WHERE t.id=NEW.task_root_id AND t.root_id=t.id AND t.task_type='activation_catchup' AND t.domain_request_id=NEW.id)
               OR (to_jsonb(NEW)-ARRAY['task_root_id','source_snapshot_id','version','updated_at','actor_id','correlation_id'])
                 IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['task_root_id','source_snapshot_id','version','updated_at','actor_id','correlation_id']) THEN
                RAISE EXCEPTION 'Invalid catch-up input binding' USING ERRCODE='23514'; END IF;
        ELSIF NOT EXISTS(SELECT 1 FROM stewardship_catchup_checkpoint k WHERE k.demand_id=NEW.id
            AND k.sequence=OLD.groups_completed+1 AND NEW.groups_completed=k.sequence
            AND NEW.items_completed=OLD.items_completed+k.items AND NEW.cursor=k.cursor AND NEW.phase=k.phase
            AND (NEW.completed_at IS NOT NULL)=k.complete AND NEW.failure_code=''
            AND k.actor_id IS NOT DISTINCT FROM NEW.actor_id AND k.correlation_id=NEW.correlation_id) THEN
            RAISE EXCEPTION 'Catch-up progress requires exact checkpoint' USING ERRCODE='23514';
        END IF;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER stewardship_catchup_guard_v1 BEFORE INSERT OR UPDATE OR DELETE ON stewardship_activation_catchup
FOR EACH ROW EXECUTE FUNCTION stewardship_catchup_guard_v1();

CREATE FUNCTION stewardship_checkpoint_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE d stewardship_activation_catchup%ROWTYPE; t stewardship_task_run%ROWTYPE; r stewardship_system_configuration%ROWTYPE;
BEGIN
    PERFORM pg_advisory_xact_lock(736220,1);
    SELECT * INTO r FROM stewardship_system_configuration FOR UPDATE;
    SELECT * INTO d FROM stewardship_activation_catchup WHERE id=NEW.demand_id FOR UPDATE;
    SELECT * INTO t FROM stewardship_task_run WHERE id=NEW.task_id FOR UPDATE;
    IF d.completed_at IS NOT NULL OR NEW.sequence<>d.groups_completed+1 OR NEW.group_key='' OR NEW.cursor='' OR NEW.phase=''
       OR t.id IS NULL OR d.task_root_id IS NULL OR t.root_id<>d.task_root_id OR t.state<>'running'
       OR t.fence<>NEW.fence OR t.worker_id IS DISTINCT FROM NEW.actor_id OR t.lease_expires_at<=clock_timestamp()
       OR r.restore_review_required OR r.current_campaign_id IS DISTINCT FROM d.campaign_id
       OR EXISTS(SELECT 1 FROM stewardship_campaign_work_gate WHERE campaign_id=d.campaign_id AND state IN ('preparing','running','tombstone')) THEN
        RAISE EXCEPTION 'Catch-up checkpoint requires current fenced input' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER stewardship_checkpoint_guard_v1 BEFORE INSERT ON stewardship_catchup_checkpoint
FOR EACH ROW EXECUTE FUNCTION stewardship_checkpoint_guard_v1();

CREATE FUNCTION stewardship_checkpoint_effect_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
    UPDATE stewardship_activation_catchup SET groups_completed=NEW.sequence,items_completed=items_completed+NEW.items,
        cursor=NEW.cursor,phase=NEW.phase,failure_code='',completed_at=CASE WHEN NEW.complete THEN stewardship_campaign_now_v1() END,
        version=version+1,actor_id=NEW.actor_id,correlation_id=NEW.correlation_id WHERE id=NEW.demand_id;
    RETURN NEW;
END $$;
CREATE TRIGGER stewardship_checkpoint_effect_v1 AFTER INSERT ON stewardship_catchup_checkpoint
FOR EACH ROW EXECUTE FUNCTION stewardship_checkpoint_effect_v1();
"""


class Migration(migrations.Migration):
    dependencies = [("stewardship_campaigns", "0008_checkpoint_completion")]
    operations = [
        migrations.RunSQL(
            SQL,
            reverse_sql="""
DROP TRIGGER stewardship_checkpoint_effect_v1 ON stewardship_catchup_checkpoint;
DROP FUNCTION stewardship_checkpoint_effect_v1();
DROP TRIGGER stewardship_checkpoint_guard_v1 ON stewardship_catchup_checkpoint;
DROP FUNCTION stewardship_checkpoint_guard_v1();
DROP TRIGGER stewardship_catchup_guard_v1 ON stewardship_activation_catchup;
DROP FUNCTION stewardship_catchup_guard_v1();
DROP TRIGGER stewardship_boundary_audit_v1 ON stewardship_campaign_boundary;
DROP FUNCTION stewardship_boundary_audit_v1();
DROP TRIGGER stewardship_boundary_guard_v1 ON stewardship_campaign_boundary;
DROP FUNCTION stewardship_boundary_guard_v1();
""",
        )
    ]
