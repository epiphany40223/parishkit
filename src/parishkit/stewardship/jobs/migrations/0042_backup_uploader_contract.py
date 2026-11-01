"""Forward migration: the off-site backup uploader's contract (#235).

Its SQL is the frozen file
``schema/migrations/0195_backup_uploader_contract.sql`` (prefix 0195 in the
repository-wide file sequence), read whole and never parsed. It revokes the
backup login's INSERT on the off-site copy outcomes and its table INSERT on
the backup run record (``database-grants`` then grants the run's column
INSERT without ``completed_at``), and INSERT on both from PUBLIC. It ends
with a DO block that refuses to commit unless neither is left. No model
state changes. Reversing needs its own forward migration.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0195_backup_uploader_contract.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The migration that installs the previous frozen file (0193), so
        # the files apply in prefix order.
        ("stewardship_jobs", "0041_backup_uploader_preparation"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
