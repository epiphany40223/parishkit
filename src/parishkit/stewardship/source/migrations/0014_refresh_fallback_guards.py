"""An immutable delta-to-full dependency requires its live creating worker claim."""

from django.db import migrations

from parishkit.stewardship.storage_migrations import immutable_guard_v1

FORWARD = """
CREATE FUNCTION stewardship_refresh_fallback_guard_v1()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
        AND mode='ExclusiveLock' AND granted)
       OR NOT EXISTS (
        SELECT 1 FROM stewardship_source_refresh_request r
        JOIN stewardship_task_run t ON t.id=NEW.task_id
        JOIN stewardship_source_refresh_command command ON command.id=NEW.command_id
        JOIN stewardship_source_refresh_request target ON target.id=command.request_id
        JOIN stewardship_system_configuration c
          ON c.current_campaign_id IS NOT DISTINCT FROM r.campaign_id
        JOIN stewardship_applied_integration i
          ON i.configuration_id=c.active_configuration_id
        WHERE r.id=NEW.request_id AND r.kind='delta'
          AND i.kind='parishsoft'
          AND i.settings->>'organization_id'=r.organization_id::text
          AND NOT c.restore_review_required
          AND r.window_canonical=stewardship_source_current_window_v1(r.campaign_id)
          AND NOT EXISTS (SELECT 1 FROM stewardship_campaign_work_gate
              WHERE state IN ('preparing','running'))
          AND t.root_id=r.task_root_id AND t.domain_request_id=r.id
          AND t.task_type='source_refresh' AND t.state='running'
          AND t.fence=NEW.task_fence AND t.lease_expires_at > clock_timestamp()
          AND NEW.actor_id=t.worker_id
          AND command.kind='full' AND command.cause='fallback'
          AND command.actor_id IS NULL AND target.kind='full'
          AND target.organization_id=r.organization_id
          AND target.campaign_id IS NOT DISTINCT FROM r.campaign_id
          AND target.window_digest=r.window_digest AND target.id<>r.id
       ) THEN
        RAISE EXCEPTION 'Source fallback requires current request/worker/dependency'
            USING ERRCODE='23514';
    END IF;
    IF NEW.reason='no_base' THEN
        IF NEW.attempt_id IS NOT NULL OR NOT EXISTS (
            SELECT 1 FROM stewardship_source_current WHERE snapshot_id IS NULL
        ) THEN
            RAISE EXCEPTION 'No-base fallback requires absent current source'
                USING ERRCODE='23514';
        END IF;
    ELSIF NEW.reason='incomplete_delta' THEN
        IF NOT EXISTS (
            SELECT 1 FROM stewardship_source_refresh_attempt a
            JOIN stewardship_source_snapshot s ON s.id=a.snapshot_id
            WHERE a.id=NEW.attempt_id AND a.request_id=NEW.request_id
              AND a.task_id=NEW.task_id AND a.task_fence=NEW.task_fence
              AND s.state='rejected' AND s.kind='delta'
        ) THEN
            RAISE EXCEPTION 'Delta fallback requires its rejected concrete attempt'
                USING ERRCODE='23514';
        END IF;
    ELSE
        RAISE EXCEPTION 'Unknown source fallback reason' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER stewardship_refresh_fallback_insert
BEFORE INSERT ON stewardship_source_refresh_fallback
FOR EACH ROW EXECUTE FUNCTION stewardship_refresh_fallback_guard_v1();
"""

REVERSE = """
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM stewardship_source_refresh_fallback) THEN
        RAISE EXCEPTION 'Source fallback history prevents schema downgrade'
            USING ERRCODE='23514';
    END IF;
END $$;
DROP TRIGGER stewardship_refresh_fallback_insert ON stewardship_source_refresh_fallback;
DROP FUNCTION stewardship_refresh_fallback_guard_v1();
"""


class Migration(migrations.Migration):
    dependencies = [("stewardship_source", "0013_sourcerefreshfallback")]
    operations = [
        immutable_guard_v1("stewardship_source_refresh_fallback"),
        migrations.RunSQL(FORWARD, REVERSE),
    ]
