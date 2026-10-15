"""Forward migration: Additional information counts its requests by follow-up state.

Its SQL is the frozen file
``schema/migrations/0100_information_status_counts.sql`` (prefix 0100,
assigned in the repository-wide file sequence), read whole and never parsed
(#720). It replaces ``stewardship_information_report_v2`` in place with
0094's body plus a ``counts`` object (completed, needed and not completed,
not needed and not completed) over the same filtered rows as ``total``, so
the page's Follow-up status chart needs no second read. It ends with a DO
block that refuses to commit unless the selection is installed as declared
with its grants and settings unchanged. No model changes, so there is no
state operation. Reversing needs its own forward migration, so, like the
baseline, it has no reverse operation.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0100_information_status_counts.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_reports", "0027_canonical_timezone"),
        # The migration that installed the previous frozen file (0099, the
        # WorkGroup rename keep), so the files apply in prefix order.
        ("stewardship_campaigns", "0012_workgroup_rename_keep"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
