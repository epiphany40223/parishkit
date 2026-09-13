"""A newer drained source owner can reject abandoned staging, never promote it."""

from importlib import import_module

from django.db import migrations

BASE = import_module(
    "parishkit.stewardship.source.migrations.0005_source_snapshot_guards"
).FORWARD
ORIGINAL = (
    "CREATE OR REPLACE FUNCTION stewardship_source_snapshot_guard()"
    + BASE.split("CREATE FUNCTION stewardship_source_snapshot_guard()", 1)[1].split(
        "CREATE TRIGGER source_snapshot_guard", 1
    )[0]
)
MARKER = "    IF NOT EXISTS (SELECT 1 FROM stewardship_source_lease l\n"
REJECTION = """
    IF TG_OP='UPDATE' AND NEW.state='rejected' THEN
        IF (to_jsonb(NEW)-ARRAY[
                'version','updated_at','actor_id','correlation_id','state'])
           IS DISTINCT FROM
           (to_jsonb(OLD)-ARRAY['version','updated_at','actor_id','correlation_id','state'])
           OR NOT EXISTS (
            SELECT 1 FROM stewardship_source_lease l
            JOIN stewardship_task_run t ON t.id=l.owner_id
            WHERE l.phase IN ('full','delta') AND l.fence >= OLD.source_fence
              AND l.expires_at > clock_timestamp() AND t.state='running'
              AND t.fence=l.task_fence AND t.worker_id=l.worker_id
              AND t.lease_expires_at > clock_timestamp()
              AND NEW.actor_id=l.worker_id
              AND (l.fence > OLD.source_fence OR (
                   l.owner_id=OLD.task_id AND l.phase=OLD.kind))
           ) THEN
            RAISE EXCEPTION 'Source rejection requires unchanged evidence/live owner'
                USING ERRCODE='23514';
        END IF;
        -- A newer source fence is possible only after prior external drainage.
        -- Keep the old task/fence binding; never rebind its payloads to recovery.
        RETURN NEW;
    END IF;
"""
assert ORIGINAL.count(MARKER) == 1
FORWARD = ORIGINAL.replace(MARKER, REJECTION + MARKER)


class Migration(migrations.Migration):
    dependencies = [("stewardship_source", "0009_refresh_request_guards")]
    # Rejected rows already existed in the old state machine. Their immutable
    # evidence remains readable under the old guard, so reversal loses no data.
    operations = [migrations.RunSQL(FORWARD, ORIGINAL)]
