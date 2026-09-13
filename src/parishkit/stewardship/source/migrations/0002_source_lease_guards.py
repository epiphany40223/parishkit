"""Enforce fencing and safe takeover even when a caller bypasses ORM services."""

from django.db import migrations

from parishkit.stewardship.storage_migrations import mutable_guard_v1


def seed_lease(apps, schema_editor):
    """Initialize one idle ownership row without inventing a task or worker."""
    apps.get_model("stewardship_source", "SourceMutationLease").objects.get_or_create(
        singleton=True
    )


FORWARD = """
CREATE FUNCTION stewardship_source_lease_owner_guard()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE instant timestamptz := clock_timestamp();
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'Source ownership cannot be deleted' USING ERRCODE='23514';
    END IF;
    IF TG_OP = 'INSERT' THEN
        IF NEW.owner_id IS NOT NULL OR NEW.fence <> 0 OR NEW.task_fence <> 0
           OR NEW.external_deadline IS NOT NULL OR NEW.acquired_at IS NOT NULL
           OR NEW.heartbeat_at IS NOT NULL THEN
            RAISE EXCEPTION 'Source ownership must start idle' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.fence = OLD.fence + 1 AND NEW.owner_id IS NOT NULL THEN
        IF OLD.expires_at > instant OR OLD.external_deadline > instant
           OR NEW.acquired_at < COALESCE(OLD.acquired_at, NEW.acquired_at)
           OR NEW.heartbeat_at <> NEW.acquired_at
           OR NEW.external_deadline IS NOT NULL THEN
            RAISE EXCEPTION 'Source takeover is not safe' USING ERRCODE='23514';
        END IF;
    ELSIF NEW.fence = OLD.fence AND OLD.owner_id IS NOT NULL THEN
        IF OLD.expires_at <= instant
           OR NEW.task_fence <> OLD.task_fence
           OR NEW.acquired_at IS DISTINCT FROM OLD.acquired_at
           OR NEW.heartbeat_at IS NULL OR NEW.heartbeat_at < OLD.heartbeat_at
           OR (OLD.external_deadline IS NOT NULL AND
               (NEW.external_deadline IS NULL OR
                NEW.external_deadline < OLD.external_deadline))
           OR (NEW.owner_id IS NOT NULL AND (
               NEW.owner_id <> OLD.owner_id OR NEW.worker_id <> OLD.worker_id
               OR NEW.phase <> OLD.phase OR NEW.expires_at < OLD.expires_at)) THEN
            RAISE EXCEPTION 'Source fence is no longer current' USING ERRCODE='23514';
        END IF;
    ELSE
        RAISE EXCEPTION 'Source fence transition is invalid' USING ERRCODE='23514';
    END IF;
    IF NEW.owner_id IS NOT NULL THEN
        IF NEW.heartbeat_at > instant OR NEW.expires_at <= instant
           OR NOT EXISTS (
            SELECT 1 FROM stewardship_task_run t WHERE t.id=NEW.owner_id
              AND t.state='running' AND t.worker_id=NEW.worker_id
              AND t.fence=NEW.task_fence AND t.lease_expires_at > instant
           ) THEN
            RAISE EXCEPTION 'Source owner requires a live task' USING ERRCODE='23514';
        END IF;
    ELSIF NOT EXISTS (
        SELECT 1 FROM stewardship_task_run t WHERE t.id=OLD.owner_id
          AND t.state='running' AND t.worker_id=OLD.worker_id
          AND t.fence=OLD.task_fence AND t.lease_expires_at > instant
    ) THEN
        RAISE EXCEPTION 'Only a live source owner may release' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER stewardship_source_lease_owner
BEFORE INSERT OR UPDATE OR DELETE ON stewardship_source_lease
FOR EACH ROW EXECUTE FUNCTION stewardship_source_lease_owner_guard();
"""

REVERSE = """
DROP TRIGGER stewardship_source_lease_owner ON stewardship_source_lease;
DROP FUNCTION stewardship_source_lease_owner_guard();
"""


class Migration(migrations.Migration):
    dependencies = [("stewardship_source", "0001_initial")]
    operations = [
        mutable_guard_v1("stewardship_source_lease", frozen_fields=("singleton",)),
        migrations.RunSQL(FORWARD, REVERSE),
        migrations.RunPython(seed_lease, migrations.RunPython.noop),
    ]
