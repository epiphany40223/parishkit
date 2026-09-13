"""Let the exact source worker acknowledge validated staging, never configure."""

from importlib import import_module

from django.db import migrations

_previous = import_module(
    "parishkit.stewardship.accounts.migrations.0062_setup_delete_guard_order"
)
BACKWARD = _previous.FORWARD
_entry = "    IF current_user='pk_stewardship_scheduler' AND\n"
_worker = """
    IF current_user='pk_stewardship_worker' THEN
        IF TG_OP<>'UPDATE' OR OLD.state<>'loading' OR NEW.state<>'collecting'
           OR NEW.actor_id IS DISTINCT FROM OLD.owner_id
           OR NEW.renewed_at IS DISTINCT FROM OLD.renewed_at
           OR NEW.source_task_id IS DISTINCT FROM OLD.source_task_id
           OR NOT EXISTS (
                SELECT 1 FROM public.stewardship_setup_source_result result
                JOIN public.stewardship_setup_source_exchange exchange
                    ON exchange.id=result.exchange_id AND exchange.attempt_id=OLD.id
                    AND exchange.scrubbed_at IS NULL
                JOIN public.stewardship_task_run finished
                    ON finished.id=exchange.task_id AND finished.state='succeeded'
                    AND finished.root_id=OLD.source_task_id
                    AND finished.fence=exchange.task_fence
                JOIN public.stewardship_source_snapshot snapshot
                    ON snapshot.id=result.snapshot_id AND snapshot.state='ready'
                    AND snapshot.task_id=finished.id
                    AND snapshot.source_fence=exchange.source_fence
            ) THEN
            RAISE EXCEPTION 'Worker may only acknowledge validated setup staging'
                USING ERRCODE='23514';
        END IF;
    END IF;
"""
if BACKWARD.count(_entry) != 1:
    raise RuntimeError("Frozen setup scheduler restriction is unavailable.")
FORWARD = BACKWARD.replace(_entry, _worker + _entry)


class Migration(migrations.Migration):
    dependencies = [("stewardship_accounts", "0070_setup_source_result")]
    operations = [migrations.RunSQL(FORWARD, BACKWARD)]
