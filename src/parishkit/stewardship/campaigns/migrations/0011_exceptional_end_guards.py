"""End edits and reopen commit with the exact selected YAML and runtime history."""

# ruff: noqa: E501
from django.db import migrations

from parishkit.stewardship.storage_migrations import immutable_guard_v1


def extend_functions(apps, editor):
    """Extend this branch's frozen guards without weakening unrelated structural locks."""
    replacements = {
        "stewardship_campaign_pointer_v1": [
            (
                "ARRAY['name','year_label','content_versions']",
                "ARRAY['name','year_label','content_versions','end_date']",
                2,  # Both sides of the structural comparison must change.
            )
        ],
        "stewardship_campaign_transition_v1": [
            (
                "WHEN 'archive' THEN",
                """WHEN 'reopen' THEN
            IF c.state<>'closed' OR NEW.token_generation_id IS NULL OR instant>=p.ends_at OR instant<p.starts_at
               OR NOT stewardship_campaign_quiet_v1(c.id) OR NOT EXISTS (
                   SELECT 1 FROM stewardship_campaign_config_intent i JOIN stewardship_config_activation a ON a.request_id=i.request_id
                   JOIN stewardship_campaign_configuration prior ON prior.id=i.prior_projection_id
                   WHERE a.configuration_id=NEW.configuration_id AND i.action='reopen' AND i.campaign_id=c.id
                   AND i.prior_projection_id=NEW.prior_projection_id AND p.starts_at=prior.starts_at AND p.ends_at>prior.ends_at
                   AND i.token_generation_id=NEW.token_generation_id AND i.expected_version+1=c.version
                   AND i.expected_runtime_version+1=r.version AND i.actor_id=NEW.actor_id
               ) THEN RAISE EXCEPTION 'Reopen requires exact extended configuration intent' USING ERRCODE='23514'; END IF;
            expected_state:='active'; expected_mode:='production';
        WHEN 'archive' THEN""",
                1,
            )
        ],
        "stewardship_campaign_transition_effect_v1": [
            (
                "NEW.action IN ('activate','withdraw')",
                "NEW.action IN ('activate','withdraw','reopen')",
                1,
            ),
            (
                "CASE WHEN NEW.action='activate' THEN NEW.token_generation_id",
                "CASE WHEN NEW.action IN ('activate','reopen') THEN NEW.token_generation_id",
                1,
            ),
        ],
    }
    for name, changes in replacements.items():
        with editor.connection.cursor() as cursor:
            cursor.execute("SELECT pg_get_functiondef(%s::regprocedure)", [name + "()"])
            sql = cursor.fetchone()[0]
        for old, new, expected_count in changes:
            if sql.count(old) != expected_count:
                raise RuntimeError("Exceptional end guard predecessor is inconsistent.")
            sql = sql.replace(old, new)
        editor.execute(sql, params=None)


def restore_functions(apps, editor):
    """Restore exact predecessor definitions, refusing to erase exceptional history."""
    from importlib import import_module

    editor.execute("""DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM stewardship_campaign_config_intent) THEN
            RAISE EXCEPTION 'Exceptional configuration history prevents reversal' USING ERRCODE='23514'; END IF;
    END $$;""")
    sql = import_module(
        "parishkit.stewardship.campaigns.migrations.0006_runtime_guards"
    ).SQL
    for name in (
        "stewardship_campaign_pointer_v1",
        "stewardship_campaign_transition_v1",
        "stewardship_campaign_transition_effect_v1",
    ):
        marker = "FUNCTION " + name + "()"
        start = sql.index(marker)
        end = sql.index("END $$;", start) + len("END $$;")
        editor.execute("CREATE OR REPLACE " + sql[start:end], params=None)


SQL = """
CREATE FUNCTION stewardship_campaign_intent_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE c stewardship_campaign%ROWTYPE; r stewardship_system_configuration%ROWTYPE;
BEGIN
    PERFORM pg_advisory_xact_lock(736220,1);
    SELECT * INTO r FROM stewardship_system_configuration FOR UPDATE;
    SELECT * INTO c FROM stewardship_campaign WHERE id=NEW.campaign_id FOR UPDATE;
    IF c.id IS DISTINCT FROM r.current_campaign_id OR c.version<>NEW.expected_version OR r.version<>NEW.expected_runtime_version
       OR c.active_configuration_id<>NEW.prior_projection_id OR r.restore_review_required OR NEW.actor_id IS NULL
       OR (NEW.action='reopen' AND (c.state<>'closed' OR NEW.token_generation_id IS NULL OR NOT stewardship_campaign_quiet_v1(c.id)))
       OR (NEW.action='edit_end' AND (c.state NOT IN ('scheduled','active') OR NEW.token_generation_id IS NOT NULL))
       OR NEW.action NOT IN ('edit_end','reopen')
       OR NOT EXISTS(SELECT 1 FROM stewardship_config_request q
           WHERE q.id=NEW.request_id AND q.actor_id=NEW.actor_id AND q.base_id=r.active_configuration_id)
    THEN RAISE EXCEPTION 'Invalid exceptional campaign configuration intent' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER stewardship_campaign_intent_v1 BEFORE INSERT ON stewardship_campaign_config_intent
FOR EACH ROW EXECUTE FUNCTION stewardship_campaign_intent_v1();

CREATE FUNCTION stewardship_campaign_end_admission_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE c stewardship_campaign%ROWTYPE; prior stewardship_campaign_configuration%ROWTYPE;
    proposed stewardship_campaign_configuration%ROWTYPE; i stewardship_campaign_config_intent%ROWTYPE;
BEGIN
    IF NEW.active_configuration_id IS NOT DISTINCT FROM OLD.active_configuration_id OR OLD.current_campaign_id IS NULL THEN RETURN NEW; END IF;
    SELECT * INTO c FROM stewardship_campaign WHERE id=OLD.current_campaign_id FOR UPDATE;
    SELECT * INTO prior FROM stewardship_campaign_configuration WHERE id=c.active_configuration_id;
    SELECT * INTO proposed FROM stewardship_campaign_configuration WHERE configuration_id=NEW.active_configuration_id AND record_id=c.id;
    IF c.structural_locked AND proposed.ends_at<>prior.ends_at THEN
        SELECT intent.* INTO i FROM stewardship_campaign_config_intent intent
            JOIN stewardship_config_activation a ON a.request_id=intent.request_id WHERE a.configuration_id=NEW.active_configuration_id;
        IF i.id IS NULL OR i.campaign_id<>c.id OR i.expected_version<>c.version OR i.expected_runtime_version<>OLD.version
           OR i.prior_projection_id<>prior.id OR i.actor_id IS DISTINCT FROM NEW.actor_id OR NEW.restore_review_required
           OR (i.action='edit_end' AND (c.state NOT IN ('scheduled','active') OR stewardship_campaign_now_v1()>=prior.ends_at))
           OR (i.action='reopen' AND (c.state<>'closed' OR proposed.ends_at<=prior.ends_at))
           OR proposed.ends_at<=stewardship_campaign_now_v1()
           OR EXISTS(SELECT 1 FROM stewardship_campaign_boundary b JOIN stewardship_task_run t ON t.id=b.task_id
               WHERE b.campaign_id=c.id AND b.kind='close' AND b.state='pending' AND t.state IN ('running','abandoned'))
           OR EXISTS(SELECT 1 FROM stewardship_campaign_work_gate WHERE state IN ('preparing','running')) THEN
            RAISE EXCEPTION 'End change requires current quiescent exceptional intent' USING ERRCODE='23514'; END IF;
    ELSIF EXISTS(SELECT 1 FROM stewardship_campaign_config_intent intent_row JOIN stewardship_config_activation a ON a.request_id=intent_row.request_id
        WHERE a.configuration_id=NEW.active_configuration_id) THEN
        RAISE EXCEPTION 'Exceptional end intent must change the end date' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER stewardship_campaign_end_admission_v1 BEFORE UPDATE ON stewardship_system_configuration
FOR EACH ROW EXECUTE FUNCTION stewardship_campaign_end_admission_v1();

CREATE FUNCTION stewardship_campaign_end_effect_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE c stewardship_campaign%ROWTYPE; p stewardship_campaign_configuration%ROWTYPE;
    i stewardship_campaign_config_intent%ROWTYPE;
BEGIN
    IF NEW.active_configuration_id IS NOT DISTINCT FROM OLD.active_configuration_id OR NEW.current_campaign_id IS NULL THEN RETURN NEW; END IF;
    SELECT * INTO c FROM stewardship_campaign WHERE id=NEW.current_campaign_id;
    SELECT * INTO p FROM stewardship_campaign_configuration WHERE id=c.active_configuration_id;
    UPDATE stewardship_campaign_boundary SET state='skipped',reason='boundary_replaced',completed_at=stewardship_campaign_now_v1(),
        version=version+1,actor_id=NEW.actor_id,correlation_id=NEW.correlation_id
    WHERE campaign_id=c.id AND kind='close' AND due_at<>p.ends_at AND state='pending';
    SELECT intent.* INTO i FROM stewardship_campaign_config_intent intent
        JOIN stewardship_config_activation a ON a.request_id=intent.request_id WHERE a.configuration_id=NEW.active_configuration_id;
    IF i.action='reopen' THEN
        INSERT INTO stewardship_campaign_transition(id,campaign_id,request_id,action,expected_version,expected_runtime_version,
            before_state,after_state,before_mode,after_mode,configuration_id,prior_projection_id,token_generation_id,reason,actor_id,correlation_id)
        VALUES(gen_random_uuid(),c.id,i.request_id,'reopen',c.version,NEW.version,'closed','active',NEW.mode,'production',
            NEW.active_configuration_id,i.prior_projection_id,i.token_generation_id,'',NEW.actor_id,NEW.correlation_id);
    END IF;
    RETURN NEW;
END $$;
-- PostgreSQL runs same-event triggers in name order. This must follow
-- stewardship_campaign_activate_v1 (new campaign projection) and
-- zzz_stewardship_schedules_activate_v1 (selected schedule revisions). Reading
-- ends_at or emitting reopen before those effects would use stale authority.
CREATE TRIGGER zzzz_stewardship_campaign_end_effect_v1 AFTER UPDATE ON stewardship_system_configuration
FOR EACH ROW EXECUTE FUNCTION stewardship_campaign_end_effect_v1();
"""


class Migration(migrations.Migration):
    dependencies = [("stewardship_campaigns", "0010_configuration_intent")]
    operations = [
        migrations.RunPython(extend_functions, restore_functions),
        immutable_guard_v1("stewardship_campaign_config_intent"),
        migrations.RunSQL(
            SQL,
            reverse_sql="""
DROP TRIGGER zzzz_stewardship_campaign_end_effect_v1 ON stewardship_system_configuration;
DROP FUNCTION stewardship_campaign_end_effect_v1();
DROP TRIGGER stewardship_campaign_end_admission_v1 ON stewardship_system_configuration;
DROP FUNCTION stewardship_campaign_end_admission_v1();
DROP TRIGGER stewardship_campaign_intent_v1 ON stewardship_campaign_config_intent;
DROP FUNCTION stewardship_campaign_intent_v1();
""",
        ),
    ]
