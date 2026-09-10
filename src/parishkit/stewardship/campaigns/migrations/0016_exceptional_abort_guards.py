"""Explicit abort restores only a candidate's still-applied predecessor."""

# ruff: noqa: E501
from django.db import migrations

from parishkit.stewardship.storage_migrations import immutable_guard_v1


def extend_checkpoint(apps, editor):
    """Only a journalled exceptional abort may fail after YAML selection."""
    with editor.connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_get_functiondef('stewardship_request_checkpoint_v2()'::regprocedure)"
        )
        sql = cursor.fetchone()[0]
    old = "AND NEW.state = 'applied')"
    new = """AND (NEW.state = 'applied' OR (NEW.state='failed' AND NEW.failure_code='invalid_candidate'
                        AND EXISTS(SELECT 1 FROM stewardship_campaign_config_abort b
                            JOIN stewardship_campaign_config_intent i ON i.id=b.intent_id WHERE i.request_id=NEW.request_id))))"""
    if sql.count(old) != 1:
        raise RuntimeError("Exceptional abort checkpoint predecessor is inconsistent.")
    editor.execute(sql.replace(old, new), params=None)


def restore_checkpoint(apps, editor):
    """A committed abort decision cannot be discarded by downgrade."""
    editor.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM stewardship_campaign_config_abort) THEN
        RAISE EXCEPTION 'Exceptional abort history prevents reversal' USING ERRCODE='23514'; END IF; END $$;""")
    with editor.connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_get_functiondef('stewardship_request_checkpoint_v2()'::regprocedure)"
        )
        sql = cursor.fetchone()[0]
    start = sql.index("AND (NEW.state = 'applied' OR (NEW.state='failed'")
    end = sql.index("i.request_id=NEW.request_id))))", start) + len(
        "i.request_id=NEW.request_id))))"
    )
    editor.execute(sql[:start] + "AND NEW.state = 'applied')" + sql[end:], params=None)


SQL = """
CREATE FUNCTION stewardship_exceptional_abort_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE i stewardship_campaign_config_intent%ROWTYPE; q stewardship_config_request%ROWTYPE;
    r stewardship_system_configuration%ROWTYPE; checkpoint_state text;
BEGIN
    PERFORM pg_advisory_xact_lock(736220,1);
    SELECT * INTO r FROM stewardship_system_configuration FOR UPDATE;
    SELECT * INTO i FROM stewardship_campaign_config_intent WHERE id=NEW.intent_id;
    SELECT * INTO q FROM stewardship_config_request WHERE id=i.request_id FOR UPDATE;
    SELECT state INTO checkpoint_state FROM stewardship_config_checkpoint WHERE request_id=q.id ORDER BY sequence DESC LIMIT 1;
    IF q.id IS NULL OR r.active_configuration_id<>q.base_id OR NEW.actor_id IS DISTINCT FROM q.actor_id OR btrim(NEW.reason)=''
       OR checkpoint_state NOT IN ('prepared','yaml_activated') OR EXISTS(SELECT 1 FROM stewardship_config_activation WHERE request_id=q.id) THEN
        RAISE EXCEPTION 'Only unapplied exceptional configuration can be aborted' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER stewardship_exceptional_abort_v1 BEFORE INSERT ON stewardship_campaign_config_abort
FOR EACH ROW EXECUTE FUNCTION stewardship_exceptional_abort_v1();

CREATE FUNCTION stewardship_aborted_activation_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
    IF EXISTS(SELECT 1 FROM stewardship_campaign_config_abort b JOIN stewardship_campaign_config_intent i ON i.id=b.intent_id
              WHERE i.request_id=NEW.request_id) THEN
        RAISE EXCEPTION 'Aborted exceptional candidate cannot be activated' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER aab_stewardship_aborted_activation_v1 BEFORE INSERT ON stewardship_config_activation
FOR EACH ROW EXECUTE FUNCTION stewardship_aborted_activation_v1();
"""


class Migration(migrations.Migration):
    dependencies = [("stewardship_campaigns", "0015_exceptional_abort")]
    operations = [
        migrations.RunPython(extend_checkpoint, restore_checkpoint),
        immutable_guard_v1("stewardship_campaign_config_abort"),
        migrations.RunSQL(
            SQL,
            reverse_sql="""
DROP TRIGGER aab_stewardship_aborted_activation_v1 ON stewardship_config_activation;
DROP FUNCTION stewardship_aborted_activation_v1();
DROP TRIGGER stewardship_exceptional_abort_v1 ON stewardship_campaign_config_abort;
DROP FUNCTION stewardship_exceptional_abort_v1();
""",
        ),
    ]
