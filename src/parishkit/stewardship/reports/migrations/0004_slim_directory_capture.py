"""Forward migration: a slimmer Family-code export snapshot (#388 L6).

Its SQL is the frozen file ``schema/migrations/0021_slim_directory_capture.sql``,
read whole and never parsed. It replaces
``stewardship_directory_export_capture_v1`` so a directory export's
immutable snapshot keeps only the private contact columns its file renders:
the address for the postal mail merge, phones for the code list filtered to
reach "neither", and the envelope number never. It ends with a DO block that
refuses to commit unless the new body is installed and still called by the
capture trigger. Existing snapshots are not rewritten. No model changes, so
there is no state operation. Reversing needs its own forward migration, so,
like the baseline, it has no reverse operation.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0021_slim_directory_capture.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The migration that installed the previous frozen file (0020, #388
        # L1), so the files apply in prefix order.
        ("stewardship_reports", "0003_directory_reach"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
