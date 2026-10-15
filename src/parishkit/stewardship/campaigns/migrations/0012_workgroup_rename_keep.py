"""Forward migration: a renamed Reminder WorkGroup keeps its exclusions (#1305).

Its SQL is the frozen file ``schema/migrations/0099_workgroup_rename_keep.sql``,
read whole and never parsed. It replaces the private paused-delivery recovery
plan view so its Reminder WorkGroup members are the current snapshot's
recorded read whichever name that read was for, as planning
(``source.workgroups.excluded_duids``) now reads them: after a rename the
previous WorkGroup's Families stay excluded until a refresh reads the new
name, and a cleared setting still excludes nobody. The column list is
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
    / "0099_workgroup_rename_keep.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_campaigns", "0011_resume_sender_check"),
        # The migration that installed the previous frozen file (0098, the
        # operator's debug clear), so the files apply in prefix order.
        ("stewardship_jobs", "0032_debug_off_clear_subject"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
