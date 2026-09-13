"""Column-limited schedulers cannot impersonate a live worker via raw UPDATE."""

from django.db import migrations

FORWARD = """
CREATE FUNCTION stewardship_task_scheduler_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
    -- Capability-based, so the same boundary covers independently named test
    -- and provisioned logins. Full worker writers retain the existing guards.
    IF NOT has_column_privilege(current_user,
            'public.stewardship_task_run', 'worker_id', 'UPDATE') THEN
        IF OLD.task_type <> 'source_refresh'
           OR OLD.state NOT IN ('queued', 'retry_wait', 'abandoned')
           OR NEW.state <> 'cancelled'
           OR NEW.action NOT IN ('safe_cancel', 'recovery_cancel')
           OR NEW.lease_expires_at IS NOT NULL
           OR NOT EXISTS(SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
                AND locktype='advisory' AND classid=736229 AND objid=1
                AND objsubid=2 AND granted)
           OR NOT EXISTS(SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
                AND locktype='advisory' AND classid=736220 AND objid=1
                AND objsubid=2 AND mode='ExclusiveLock' AND granted) THEN
            RAISE EXCEPTION 'Scheduler may only cancel waiting source work'
                USING ERRCODE='42501';
        END IF;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER stewardship_task_scheduler_v1 BEFORE UPDATE
ON public.stewardship_task_run FOR EACH ROW
EXECUTE FUNCTION stewardship_task_scheduler_v1();
"""

REVERSE = """
LOCK TABLE public.stewardship_task_run IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
    IF EXISTS(SELECT 1 FROM public.stewardship_task_run) THEN
        RAISE EXCEPTION 'Task history prevents scheduler guard downgrade'
            USING ERRCODE='23514';
    END IF;
END $$;
DROP TRIGGER stewardship_task_scheduler_v1 ON public.stewardship_task_run;
DROP FUNCTION stewardship_task_scheduler_v1();
"""


class Migration(migrations.Migration):
    dependencies = [("stewardship_jobs", "0004_task_phase_guards")]
    operations = [migrations.RunSQL(FORWARD, REVERSE)]
