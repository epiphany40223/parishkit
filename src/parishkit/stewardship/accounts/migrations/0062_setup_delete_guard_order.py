"""Reject deletion before inspecting the new tuple in the scheduler restriction."""

from importlib import import_module

from django.db import migrations

_previous = import_module(
    "parishkit.stewardship.accounts.migrations.0058_scheduler_setup_expiry"
)
BACKWARD = _previous._function.replace(_previous._read, _previous._bounded).replace(
    _previous._entry, _previous._guard
)
_scheduler = _previous._guard.removeprefix("BEGIN\n").removesuffix(
    "    IF TG_OP='DELETE' THEN"
)
_delete = """    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'Setup history cannot be deleted' USING ERRCODE='23514';
    END IF;
"""
if BACKWARD.count(_scheduler) != 1 or BACKWARD.count(_delete) != 1:
    raise RuntimeError("Frozen setup deletion guard is unavailable.")
FORWARD = BACKWARD.replace(_scheduler, "").replace(_delete, _delete + _scheduler)


class Migration(migrations.Migration):
    dependencies = [("stewardship_accounts", "0061_provider_context_owner_rls")]
    operations = [migrations.RunSQL(FORWARD, BACKWARD)]
