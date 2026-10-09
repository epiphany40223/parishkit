"""Forward migration: the directory audit records its reach filter (#388 L1).

Its SQL is the frozen file ``schema/migrations/0020_directory_reach.sql``,
read whole and never parsed. It replaces ``stewardship_safe_context_v1`` so
the ``action`` context also admits ``directory_reach`` (``any``, ``email``,
``mail`` or ``neither``), mirroring ``audit.schemas``, and ends with a DO
block that refuses to commit unless the new allowlist is installed with its
attributes and exactly that vocabulary. No model changes, so there is no
state operation. Reversing needs its own forward migration, so, like the
baseline, it has no reverse operation.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0020_directory_reach.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_reports", "0002_download_audit_context"),
        # The migration that installed the previous frozen file (0019,
        # #787), so the files apply in prefix order.
        ("stewardship_jobs", "0012_web_health"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
