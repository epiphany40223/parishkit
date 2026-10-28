"""Forward migration: settle an unconfirmed notice from evidence (#2295).

Its SQL is the frozen file ``schema/migrations/0190_settle_notices.sql``
(prefix 0190 in the repository-wide file sequence), read whole and never
parsed. It replaces the delivery resolution guard in place so that an
operational or security notice whose outcome is unknown can be marked
delivered or recorded as not sent, with a note, and never resent, stating
SECURITY DEFINER again. It ends with a DO block that refuses to commit
unless the guard is installed as declared. No model state changes.
Reversing needs its own forward migration.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0190_settle_notices.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The jobs app's previous migration.
        ("stewardship_jobs", "0038_probe_settings_changed"),
        # The migration that installs the previous frozen file (0189), so the
        # files apply in prefix order. It follows 0173 (reports 0047), whose
        # guard body this file replaces.
        ("stewardship_responses", "0033_commitment_roster_date"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
