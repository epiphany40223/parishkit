"""Orthogonal campaign controls and globally serialized purge preparation."""

# ruff: noqa: E501
from django.db import migrations

from parishkit.stewardship.storage_migrations import immutable_guard_v1


def control_writer(apps, editor):
    """Admit exact control evidence alongside, not instead of, lifecycle history."""
    with editor.connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_get_functiondef('stewardship_campaign_runtime_v1()'::regprocedure)"
        )
        sql = cursor.fetchone()[0]
    old = "AND t.actor_id IS NOT DISTINCT FROM NEW.actor_id AND t.correlation_id=NEW.correlation_id) THEN"
    new = """AND t.actor_id IS NOT DISTINCT FROM NEW.actor_id AND t.correlation_id=NEW.correlation_id)
            AND NOT EXISTS (SELECT 1 FROM stewardship_campaign_control k WHERE k.campaign_id=NEW.id AND k.expected_version=OLD.version
                AND k.actor_id IS NOT DISTINCT FROM NEW.actor_id AND k.correlation_id=NEW.correlation_id AND NEW.state=OLD.state) THEN"""
    if sql.count(old) != 1:
        raise RuntimeError("Campaign control guard predecessor is inconsistent.")
    editor.execute(sql.replace(old, new), params=None)


def restore_writer(apps, editor):
    """Refuse populated reversal before restoring the previous writer restriction."""
    from importlib import import_module

    editor.execute("""DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM stewardship_campaign_control) OR EXISTS(SELECT 1 FROM stewardship_campaign_work_gate) THEN
            RAISE EXCEPTION 'Campaign control history prevents reversal' USING ERRCODE='23514'; END IF;
    END $$;""")
    sql = import_module(
        "parishkit.stewardship.campaigns.migrations.0006_runtime_guards"
    ).SQL
    start = sql.index("CREATE OR REPLACE FUNCTION stewardship_campaign_runtime_v1()")
    end = sql.index("END $$;", start) + len("END $$;")
    editor.execute(sql[start:end], params=None)


SQL = """
CREATE FUNCTION stewardship_control_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE c stewardship_campaign%ROWTYPE; r stewardship_system_configuration%ROWTYPE; p stewardship_campaign_configuration%ROWTYPE;
BEGIN
    PERFORM pg_advisory_xact_lock(736220,1);
    SELECT * INTO r FROM stewardship_system_configuration FOR UPDATE;
    SELECT * INTO c FROM stewardship_campaign WHERE id=NEW.campaign_id FOR UPDATE;
    SELECT * INTO p FROM stewardship_campaign_configuration WHERE id=c.active_configuration_id;
    IF c.id IS DISTINCT FROM r.current_campaign_id OR c.version<>NEW.expected_version OR r.version<>NEW.expected_runtime_version
       OR NEW.actor_id IS NULL OR r.mode<>'production' OR r.restore_review_required
       OR c.state NOT IN ('scheduled','active','closed') OR NEW.occurred_at>stewardship_campaign_now_v1()
       OR EXISTS(SELECT 1 FROM stewardship_campaign_work_gate WHERE campaign_id=c.id AND state IN ('preparing','running','tombstone')) THEN
        RAISE EXCEPTION 'Campaign control has stale or blocked inputs' USING ERRCODE='23514'; END IF;
    CASE NEW.action
        WHEN 'pause' THEN
            IF c.delivery_paused OR NEW.reason='' OR NEW.evidence_id IS NOT NULL THEN RAISE EXCEPTION 'Invalid delivery pause' USING ERRCODE='23514'; END IF;
        WHEN 'resume' THEN
            IF NOT c.delivery_paused OR NEW.reason='' OR NEW.evidence_id IS NOT NULL THEN RAISE EXCEPTION 'Invalid delivery resume' USING ERRCODE='23514'; END IF;
        WHEN 'first_delivery','first_submission' THEN
            IF NEW.evidence_id IS NULL OR NEW.occurred_at<p.starts_at OR NEW.occurred_at>=p.ends_at
               OR (NEW.action='first_delivery' AND c.first_live_delivery_at IS NOT NULL)
               OR (NEW.action='first_submission' AND c.first_live_submission_at IS NOT NULL) THEN
                RAISE EXCEPTION 'Invalid first-live-effect evidence' USING ERRCODE='23514'; END IF;
        ELSE RAISE EXCEPTION 'Unknown campaign control' USING ERRCODE='23514';
    END CASE;
    RETURN NEW;
END $$;
CREATE TRIGGER stewardship_control_guard_v1 BEFORE INSERT ON stewardship_campaign_control
FOR EACH ROW EXECUTE FUNCTION stewardship_control_guard_v1();

CREATE FUNCTION stewardship_control_effect_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
    UPDATE stewardship_campaign SET
        delivery_paused=CASE WHEN NEW.action IN ('pause','resume') THEN NEW.action='pause' ELSE delivery_paused END,
        pause_version=pause_version+(CASE WHEN NEW.action IN ('pause','resume') THEN 1 ELSE 0 END),
        pause_actor_id=CASE WHEN NEW.action IN ('pause','resume') THEN NEW.actor_id ELSE pause_actor_id END,
        pause_reason=CASE WHEN NEW.action IN ('pause','resume') THEN NEW.reason ELSE pause_reason END,
        paused_at=CASE WHEN NEW.action='pause' THEN NEW.occurred_at ELSE paused_at END,
        resumed_at=CASE WHEN NEW.action='pause' THEN NULL WHEN NEW.action='resume' THEN NEW.occurred_at ELSE resumed_at END,
        first_live_delivery_at=CASE WHEN NEW.action='first_delivery' THEN NEW.occurred_at ELSE first_live_delivery_at END,
        first_live_submission_at=CASE WHEN NEW.action='first_submission' THEN NEW.occurred_at ELSE first_live_submission_at END,
        version=version+1,actor_id=NEW.actor_id,correlation_id=NEW.correlation_id WHERE id=NEW.campaign_id;
    INSERT INTO stewardship_audit_event(id,actor_id,correlation_id,event_type,subject_id,campaign_reference)
    VALUES(gen_random_uuid(),NEW.actor_id,NEW.correlation_id,'campaign_'||NEW.action,NEW.id,NEW.campaign_id);
    RETURN NEW;
END $$;
CREATE TRIGGER stewardship_control_effect_v1 AFTER INSERT ON stewardship_campaign_control
FOR EACH ROW EXECUTE FUNCTION stewardship_control_effect_v1();

CREATE FUNCTION stewardship_work_gate_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE c stewardship_campaign%ROWTYPE; r stewardship_system_configuration%ROWTYPE;
BEGIN
    PERFORM pg_advisory_xact_lock(736220,1);
    IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Work gate history cannot be deleted' USING ERRCODE='23514'; END IF;
    SELECT * INTO r FROM stewardship_system_configuration FOR UPDATE;
    SELECT * INTO c FROM stewardship_campaign WHERE id=NEW.campaign_id FOR UPDATE;
    IF NEW.actor_id IS NULL OR r.current_campaign_id IS NOT NULL OR r.mode<>'testing' OR r.restore_review_required THEN
        RAISE EXCEPTION 'Work gate requires the exclusive maintenance window' USING ERRCODE='23514'; END IF;
    IF TG_OP='INSERT' THEN
        IF NEW.state<>'preparing' OR NEW.version<>1 OR c.state<>'archived'
           OR EXISTS(SELECT 1 FROM stewardship_campaign_work_gate WHERE state IN ('preparing','running')) THEN
            RAISE EXCEPTION 'Only one global purge preparation is admitted' USING ERRCODE='23514'; END IF;
    ELSE
        IF NOT ((OLD.state='preparing' AND NEW.state='released' AND c.state='archived')
            OR (OLD.state='preparing' AND NEW.state='running' AND c.state='purging')
            OR (OLD.state='running' AND NEW.state='tombstone' AND c.state='purged')) THEN
            RAISE EXCEPTION 'Invalid work gate transition' USING ERRCODE='23514'; END IF;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER stewardship_work_gate_v1 BEFORE INSERT OR UPDATE OR DELETE ON stewardship_campaign_work_gate
FOR EACH ROW EXECUTE FUNCTION stewardship_work_gate_v1();
"""


class Migration(migrations.Migration):
    dependencies = [("stewardship_campaigns", "0012_campaign_controls")]
    operations = [
        migrations.RunPython(control_writer, restore_writer),
        immutable_guard_v1("stewardship_campaign_control"),
        migrations.RunSQL(
            SQL,
            reverse_sql="""
DROP TRIGGER stewardship_work_gate_v1 ON stewardship_campaign_work_gate;
DROP FUNCTION stewardship_work_gate_v1();
DROP TRIGGER stewardship_control_effect_v1 ON stewardship_campaign_control;
DROP FUNCTION stewardship_control_effect_v1();
DROP TRIGGER stewardship_control_guard_v1 ON stewardship_campaign_control;
DROP FUNCTION stewardship_control_guard_v1();
""",
        ),
    ]
