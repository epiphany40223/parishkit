"""Durable sanitized failure evidence never clears an unfinished catch-up hold."""

# ruff: noqa: E501

import uuid

import django.db.models.deletion
import django.db.models.functions.datetime
from django.db import migrations, models

import parishkit.stewardship.observability
import parishkit.stewardship.storage
from parishkit.stewardship.storage_migrations import immutable_guard_v1


def extend_demand(apps, editor):
    """Permit only an exact immutable failure receipt to change failure metadata."""
    with editor.connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_get_functiondef('stewardship_catchup_guard_v1()'::regprocedure)"
        )
        sql = cursor.fetchone()[0]
    old = "ELSIF NOT EXISTS(SELECT 1 FROM stewardship_catchup_checkpoint"
    new = """ELSIF EXISTS(SELECT 1 FROM stewardship_catchup_failure f WHERE f.demand_id=NEW.id
            AND f.expected_version=OLD.version AND NEW.version=OLD.version+1 AND f.code=NEW.failure_code
            AND f.actor_id IS NOT DISTINCT FROM NEW.actor_id AND f.correlation_id=NEW.correlation_id)
            AND (to_jsonb(NEW)-ARRAY['failure_code','version','updated_at','actor_id','correlation_id'])
                = (to_jsonb(OLD)-ARRAY['failure_code','version','updated_at','actor_id','correlation_id']) THEN
            RETURN NEW;
        ELSIF NOT EXISTS(SELECT 1 FROM stewardship_catchup_checkpoint"""
    if sql.count(old) != 1:
        raise RuntimeError("Catch-up failure predecessor is inconsistent.")
    editor.execute(sql.replace(old, new), params=None)


def restore_demand(apps, editor):
    """Restore the exact previous guard after refusing populated reversal."""
    from importlib import import_module

    sql = import_module(
        "parishkit.stewardship.campaigns.migrations.0009_boundary_catchup_guards"
    ).SQL
    start = sql.index("CREATE FUNCTION stewardship_catchup_guard_v1()")
    end = sql.index("END $$;", start) + len("END $$;")
    editor.execute(
        sql[start:end].replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1),
        params=None,
    )


SQL = """
CREATE FUNCTION stewardship_catchup_failure_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE d stewardship_activation_catchup%ROWTYPE; t stewardship_task_run%ROWTYPE;
BEGIN
    PERFORM pg_advisory_xact_lock(736220,1);
    PERFORM 1 FROM stewardship_system_configuration FOR UPDATE;
    SELECT * INTO d FROM stewardship_activation_catchup WHERE id=NEW.demand_id FOR UPDATE;
    SELECT * INTO t FROM stewardship_task_run WHERE id=NEW.task_id FOR UPDATE;
    IF d.id IS NULL OR d.completed_at IS NOT NULL OR NEW.expected_version<>d.version OR NEW.actor_id IS NULL
       OR NEW.code NOT IN ('source_unavailable','invalid_source','enumeration_failed','outcome_failed','recovery_required')
       OR t.id IS NULL OR d.task_root_id IS NULL OR t.root_id<>d.task_root_id OR t.state<>'running'
       OR t.fence<>NEW.fence OR t.worker_id IS DISTINCT FROM NEW.actor_id OR t.lease_expires_at<=clock_timestamp() THEN
        RAISE EXCEPTION 'Catch-up failure requires exact fenced evidence' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER stewardship_catchup_failure_guard_v1 BEFORE INSERT ON stewardship_catchup_failure
FOR EACH ROW EXECUTE FUNCTION stewardship_catchup_failure_guard_v1();
CREATE FUNCTION stewardship_catchup_failure_effect_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
    UPDATE stewardship_activation_catchup SET failure_code=NEW.code,version=version+1,
        actor_id=NEW.actor_id,correlation_id=NEW.correlation_id WHERE id=NEW.demand_id;
    INSERT INTO stewardship_audit_event(id,actor_id,correlation_id,event_type,subject_id,campaign_reference)
    SELECT gen_random_uuid(),NEW.actor_id,NEW.correlation_id,'catchup_failed',NEW.id,d.campaign_id
    FROM stewardship_activation_catchup d WHERE d.id=NEW.demand_id;
    RETURN NEW;
END $$;
CREATE TRIGGER stewardship_catchup_failure_effect_v1 AFTER INSERT ON stewardship_catchup_failure
FOR EACH ROW EXECUTE FUNCTION stewardship_catchup_failure_effect_v1();
"""


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_campaigns", "0016_exceptional_abort_guards"),
        ("stewardship_jobs", "0002_taskrun_guards"),
    ]

    operations = [
        migrations.CreateModel(
            name="CatchUpFailure",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "created_at",
                    parishkit.stewardship.storage.UTCDateTimeField(
                        db_default=django.db.models.functions.datetime.Now(),
                        editable=False,
                    ),
                ),
                ("actor_id", models.UUIDField(blank=True, editable=False, null=True)),
                (
                    "correlation_id",
                    models.UUIDField(
                        db_index=True,
                        default=parishkit.stewardship.observability.current_correlation,
                        editable=False,
                    ),
                ),
                ("expected_version", models.PositiveBigIntegerField()),
                ("fence", models.PositiveBigIntegerField()),
                ("code", models.CharField(max_length=32)),
                (
                    "demand",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="stewardship_campaigns.activationcatchupdemand",
                    ),
                ),
                (
                    "task",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="stewardship_jobs.taskrun",
                    ),
                ),
            ],
            options={
                "db_table": "stewardship_catchup_failure",
                "constraints": [
                    models.UniqueConstraint(
                        fields=("demand", "expected_version"),
                        name="catchup_failure_version",
                    )
                ],
            },
        ),
        immutable_guard_v1("stewardship_catchup_failure"),
        migrations.RunPython(extend_demand, restore_demand),
        migrations.RunSQL(
            SQL,
            reverse_sql="""
DO $$ BEGIN IF EXISTS(SELECT 1 FROM stewardship_catchup_failure) THEN
    RAISE EXCEPTION 'Catch-up failure history prevents reversal' USING ERRCODE='23514'; END IF; END $$;
DROP TRIGGER stewardship_catchup_failure_effect_v1 ON stewardship_catchup_failure;
DROP FUNCTION stewardship_catchup_failure_effect_v1();
DROP TRIGGER stewardship_catchup_failure_guard_v1 ON stewardship_catchup_failure;
DROP FUNCTION stewardship_catchup_failure_guard_v1();
""",
        ),
    ]
