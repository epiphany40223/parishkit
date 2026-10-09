"""Forward migration: the resume preview skips WorkGroup Reminders (#866).

Its SQL is the frozen file ``schema/migrations/0035_workgroup_recovery_skip.sql``,
read whole and never parsed. It replaces the private paused-delivery recovery
plan view so a Reminder WorkGroup Family's due reminders count and are written
as skipped (``workgroup_excluded``), as planning skips them, instead of being
counted as to be emailed or coalesced into the invitation. The column list is
unchanged, so the summary view and the recovery function keep reading it. It
ends with a DO block that refuses to commit unless the new view is installed
and the grants are unchanged. Reversing needs its own forward migration, so,
like the baseline, it has no reverse operation.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0035_workgroup_recovery_skip.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_campaigns", "0007_drop_initial_invitation_state"),
        # The migration that installed the previous frozen file (0034), so
        # the files apply in prefix order.
        ("stewardship_accounts", "0009_setup_without_campaign"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
