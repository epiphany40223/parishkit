"""Forward migration: Family mail preparation may claim a reminder ahead of time.

Before BG-12 (#447) the occurrence guard's fenced running-claim branch
refused every claim on an occurrence that was not yet due. The bulk Family
send now prepares a Production reminder in a lead window of two hours before
its due time (``family_schedule_planning.PREPARE_AHEAD``), so that branch
refuses a ``family_mail_prepare`` claim on a Production occurrence only when
it is due more than two hours from now. Every other task type, and Testing,
is refused exactly as before, and the dispatch guard is unchanged, so nothing
is sent before its due time.

Its SQL is the frozen file ``schema/migrations/0005_occurrence_prepare_ahead.sql``
(the next prefix in the repository-wide file sequence after accounts'
``0004_automation_sessions.sql``, hence the dependency on that migration),
read whole and never parsed: ``CREATE OR REPLACE`` of
``stewardship_occurrence_guard_v1`` with the fresh-install baseline's text
(schema/functions.sql) as it stands at this release, then a DO block that
refuses to commit unless the new body is installed, so an upgrade cannot
report success while the old guard is still in place. A digest test pins the
frozen file, and a second test checks that the latest migration's copy of the
guard still equals the baseline's. The frozen text starts with ``SET LOCAL
check_function_bodies = false``.

A fresh install runs the baseline (whose functions.sql already carries this
body) and then this migration, which re-creates the guard unchanged; an
upgraded database ends in the same catalog. Reversing needs its own forward
migration restoring the old condition, so, like the baseline, it has no
reverse operation.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0005_occurrence_prepare_ahead.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_campaigns", "0002_family_engagement"),
        # The migration that installed the previous frozen file, so the
        # files apply in prefix order.
        ("stewardship_accounts", "0004_automation_sessions"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
