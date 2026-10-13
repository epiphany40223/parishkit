"""Forward migration: refresh a staged snapshot's statistics off the lock (#895).

Its SQL is the frozen file ``schema/migrations/0086_snapshot_statistics.sql``
(prefix 0086 in the repository-wide file sequence), read whole and never
parsed. It creates the worker-only definer
``stewardship_source_snapshot_analyze_v1``, which analyzes the snapshot
membership tables for a snapshot that is still staging with its live fenced
owner, so a source refresh can refresh the planner statistics before the
ready transition instead of under the global work-order lock. It ends with
a DO block that refuses to commit unless the definer is installed as
declared and the completion statistics trigger is still attached. Nothing
changes in Django's model state. Reversing needs its own forward migration.

It follows the users swap (#952), so its definer is granted only once it is
applied (runtime_grants.CLOSURE_FUNCTIONS).
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0086_snapshot_statistics.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_source", "0009_drop_examples"),
        # source.0009 above installed the latest frozen file on main when
        # this was written (0085), so the files apply in prefix order. This
        # follows the users swap, so `migrate --users-upgrade-base` never
        # applies this file.
        ("stewardship_jobs", "0030_bounce_report"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
