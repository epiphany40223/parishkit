"""Forward migration: fresh-gated actions accept automation sessions (ADM-11 PR 5).

Its SQL is the frozen file ``schema/migrations/0013_automation_fresh_guards.sql``
(the next prefix in the repository-wide file sequence after
``0012_daily_send_count.sql``, which the dependency below installs), read
whole and never parsed. It creates ``stewardship_automation_fresh_principal_v1`` and
re-creates, copied verbatim from the fresh-install baseline, the four
session-bound guards (delivery controls, chosen-Family tests, the Production
confirmation and pre-start withdrawal) and the two secret request guards, so
each accepts a live, full-scope automation session in place of a Google
sign-in within five minutes. It ends with a DO block that refuses to commit
unless all of it is installed. No table or column changes, so there is no
model state to record. Reversing needs its own forward migration restoring
the old bodies, so, like the baseline, it has no reverse operation.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0013_automation_fresh_guards.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_accounts", "0004_automation_sessions"),
        # The migration that installs frozen file 0012, so the files apply in
        # prefix order.
        ("stewardship_jobs", "0010_daily_send_count"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
