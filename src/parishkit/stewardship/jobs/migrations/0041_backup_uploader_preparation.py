"""Forward migration: the off-site backup uploader's schema preparation (#235).

Its SQL is the frozen file
``schema/migrations/0193_backup_uploader_preparation.sql`` (prefix 0193 in
the repository-wide file sequence), read whole and never parsed. It replaces
0176's backup destination definer in place so that its caller check also
admits the uploader's login (stating SECURITY DEFINER and the PUBLIC revoke
again), and the operational log writer so that login may record timeout
events. The login itself comes with a later release, so nothing changes
until it exists. It ends with a DO block that refuses to commit unless both
are installed as declared. No model state changes. Reversing needs its own
forward migration.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0193_backup_uploader_preparation.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The jobs app's previous migration, so the app keeps one leaf.
        ("stewardship_jobs", "0040_settle_leader_emails"),
        # The migration that installs the previous frozen file (0192).
        ("stewardship_reports", "0053_late_report_totals"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
