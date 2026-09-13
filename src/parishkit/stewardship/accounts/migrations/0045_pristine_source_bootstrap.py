"""Allow only pristine migration-seeded source sentinels during first bootstrap.

Earlier transaction tests flushed these rows before bootstrap, hiding the
fresh-deployment failure. Used source ownership/pointers still prevent adoption;
this is not a blanket exemption for source or future application tables.
"""

from importlib import import_module

from django.db import migrations

_old = import_module(
    "parishkit.stewardship.accounts.migrations.0036_bootstrap_empty_database"
).FORWARD
# Reuse only this frozen function definition, not its policy/trigger creation.
_function = _old.split("CREATE FUNCTION ", 1)[1].split("-- This definer", 1)[0]
REVERSE = "CREATE OR REPLACE FUNCTION " + _function
_loop = """        EXECUTE format('SELECT EXISTS(SELECT 1 FROM %I.%I)',"""
_pristine = """
        IF relation.nspname='public'
           AND relation.relname='stewardship_source_lease' THEN
            IF EXISTS (SELECT 1 FROM public.stewardship_source_lease
                WHERE (singleton AND version=1 AND actor_id IS NULL
                    AND created_at=updated_at AND owner_id IS NULL
                    AND task_fence=0 AND worker_id IS NULL AND fence=0 AND phase='idle'
                    AND acquired_at IS NULL AND heartbeat_at IS NULL
                    AND expires_at IS NULL AND external_deadline IS NULL)
                    IS NOT TRUE) THEN
                RAISE EXCEPTION 'Initial bootstrap cannot adopt used source ownership'
                    USING ERRCODE='23514';
            END IF;
            CONTINUE;
        END IF;
        IF relation.nspname='public'
           AND relation.relname='stewardship_source_current' THEN
            IF EXISTS (SELECT 1 FROM public.stewardship_source_current
                WHERE (singleton AND version=1 AND actor_id IS NULL
                    AND created_at=updated_at AND snapshot_id IS NULL
                    AND generation=0 AND organization_id IS NULL) IS NOT TRUE) THEN
                RAISE EXCEPTION 'Initial bootstrap cannot adopt a used source pointer'
                    USING ERRCODE='23514';
            END IF;
            CONTINUE;
        END IF;
"""
if REVERSE.count(_loop) != 1:
    raise RuntimeError("Frozen bootstrap admission definition is unavailable.")
FORWARD = REVERSE.replace(_loop, _pristine + _loop)


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_accounts", "0044_chair_activation_receipts"),
        ("stewardship_source", "0005_source_snapshot_guards"),
    ]
    operations = [migrations.RunSQL(FORWARD, REVERSE)]
