"""Forward migration: the operational log admits ``consumer_readmission`` (#2228).

Its SQL is the frozen file ``schema/migrations/0187_consumer_readmission_log.sql``
(prefix 0187, assigned centrally), read whole and never parsed. It widens
the operational log's closed event list (``operational_event_safe``) with
``consumer_readmission``, the line a consumer started without a setup
credential logs before it exits to be admitted again, so every Python
event stays admitted by the database. It ends with a DO block that refuses
to commit unless the widened check is installed and validated. The check is
not in Django's model state, so no state operation is needed. Reversing
needs its own forward migration, so it has no reverse operation.

It lives in the audit app, which owns the operational log, and depends on
the migration that installs the previous frozen file, so the files apply in
prefix order. That puts it in the users swap's closure; its statements are
safe to repeat, so the swap test runs its file again after the reruns that
bring back 0106's older list (``CLOSURE_CONSTRAINTS``).
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0187_consumer_readmission_log.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The audit app's previous migration (0152, #1866).
        ("stewardship_audit", "0007_audit_actor_time"),
        # The migration that installs the previous frozen file (0186,
        # #2200), so the files apply in prefix order.
        ("stewardship_accounts", "0039_setup_backup_step"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
