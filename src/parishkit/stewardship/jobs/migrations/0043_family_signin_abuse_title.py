"""Forward migration: the Family sign-in abuse alert's title (#2330).

Its SQL is the frozen file
``schema/migrations/0202_family_signin_abuse_title.sql`` (prefix 0202,
assigned in the repository-wide file sequence), read whole and never
parsed. It creates nothing and changes no table: it replaces the
operational alert content function ``stewardship_ops_content_v2`` in
place, copied whole from 0043, so the Family sign-in abuse alert is titled
"Sustained Family sign-in abuse", as ``jobs/operational_content.py`` now
renders it. It ends with a DO block that refuses to commit unless the new
title is installed with the function's grants and settings unchanged.
Reversing needs its own forward migration.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0202_family_signin_abuse_title.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The jobs app's previous migration (frozen 0195), so the app keeps
        # one leaf.
        ("stewardship_jobs", "0042_backup_uploader_contract"),
        # The migration that installs the previous frozen file (0201), so
        # the files apply in prefix order.
        ("stewardship_campaigns", "0021_archive_settles_mail"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
