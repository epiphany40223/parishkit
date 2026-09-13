"""Permit only fenced disposal of one expired setup's unpromoted source rows."""

from importlib import import_module

from django.db import migrations

_payload = import_module(
    "parishkit.stewardship.source.migrations.0018_payload_guard_resolution"
)
_membership = import_module(
    "parishkit.stewardship.source.migrations.0020_static_membership_lookup"
)
_start = "CREATE OR REPLACE FUNCTION public.stewardship_source_payload_guard()"
PAYLOAD = _start + _payload.FORWARD.split(_start, 1)[1].split("$$;", 1)[0] + "$$;"
MEMBERSHIP = _membership.FORWARD

HELPERS = """
CREATE FUNCTION public.stewardship_setup_disposable_snapshot_v1(identifier uuid)
RETURNS boolean LANGUAGE sql VOLATILE
SET search_path=pg_catalog,public,pg_temp AS $$
    SELECT current_user='pk_stewardship_worker' AND EXISTS (
        SELECT 1 FROM public.stewardship_source_lease lease
        JOIN public.stewardship_task_run task ON task.id=lease.owner_id
        JOIN public.stewardship_setup_attempt attempt
            ON attempt.id=task.domain_request_id
        JOIN public.stewardship_source_snapshot snapshot ON snapshot.id=identifier
        JOIN public.stewardship_task_run original ON original.id=snapshot.task_id
        WHERE task.task_type='setup_source_cleanup' AND task.state='running'
            AND task.worker_id=lease.worker_id AND task.fence=lease.task_fence
            AND task.lease_expires_at>clock_timestamp()
            AND lease.phase='full' AND lease.expires_at>clock_timestamp()
            AND lease.fence>snapshot.source_fence
            AND attempt.state='expired' AND original.root_id=attempt.source_task_id
            AND original.task_type='setup_source_load'
            AND original.domain_request_id=attempt.id
            AND snapshot.state='rejected' AND snapshot.generation IS NULL
            AND NOT EXISTS (SELECT 1 FROM public.stewardship_source_current current
                WHERE current.snapshot_id=snapshot.id)
            AND EXISTS (SELECT 1 FROM public.stewardship_system_configuration
                WHERE NOT restore_review_required)
    ) AND EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
        AND mode='ExclusiveLock' AND granted);
$$;
CREATE FUNCTION public.stewardship_setup_disposable_payload_v1(
    kind text, identifier uuid)
RETURNS boolean LANGUAGE plpgsql VOLATILE
SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE permitted boolean;
BEGIN
    IF kind NOT IN ('family','member','contact','address','ministry','roster',
                    'fund','pledge','contribution') THEN RETURN false; END IF;
    -- Each payload must still have an exact expired-setup membership. The
    -- service deletes payloads before those memberships in the same transaction;
    -- deferred foreign keys reject any attempt to leave a dangling reference.
    EXECUTE format('SELECT count(*)>0 AND bool_and('
        'public.stewardship_setup_disposable_snapshot_v1(snapshot_id)) '
        'FROM public.%I WHERE payload_id=$1', 'stewardship_snapshot_'||kind)
        INTO permitted USING identifier;
    RETURN COALESCE(permitted,false);
END $$;
"""
_marker = "    IF TG_OP = 'DELETE' THEN\n"
_payload_guard = """        IF current_user='pk_stewardship_worker' THEN
            IF NOT public.stewardship_setup_disposable_payload_v1(
                substring(TG_TABLE_NAME from length('stewardship_source_')+1), OLD.id)
            THEN
                RAISE EXCEPTION 'Worker deletion requires exact expired setup payload'
                    USING ERRCODE='23514';
            END IF;
            RETURN OLD;
        END IF;
"""
_membership_guard = """        IF current_user='pk_stewardship_worker'
           AND NOT public.stewardship_setup_disposable_snapshot_v1(OLD.snapshot_id)
        THEN
            RAISE EXCEPTION 'Worker deletion requires exact expired setup membership'
                USING ERRCODE='23514';
        END IF;
"""
if PAYLOAD.count(_marker) != 1 or MEMBERSHIP.count(_marker) != 1:
    raise RuntimeError("Frozen source deletion guards are unavailable.")
FORWARD = (
    HELPERS
    + PAYLOAD.replace(_marker, _marker + _payload_guard)
    + MEMBERSHIP.replace(_marker, _marker + _membership_guard)
)
BACKWARD = (
    PAYLOAD
    + MEMBERSHIP
    + """
DROP FUNCTION public.stewardship_setup_disposable_payload_v1(text,uuid);
DROP FUNCTION public.stewardship_setup_disposable_snapshot_v1(uuid);
"""
)


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_source", "0020_static_membership_lookup"),
        ("stewardship_accounts", "0071_setup_worker_completion"),
    ]
    operations = [migrations.RunSQL(FORWARD, BACKWARD)]
