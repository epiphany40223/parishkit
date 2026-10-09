"""Forward migration: the directory search finds Members' names and envelopes (#664).

Its SQL is the frozen file ``schema/migrations/0022_directory_member_search.sql``
(prefix 0022, assigned in the repository-wide file sequence), read whole and
never parsed. It replaces ``stewardship_directory_report_v1``, the selection
behind the Family directory, its exports and the Find a Family box, so its
search also matches any active Member's name and the envelope number. The
signature, attributes and grants are unchanged, and the file ends with a DO
block that refuses to commit unless the new body is installed with them. No
model changes, so there are no state operations. Reversing needs its own
forward migration, so, like the baseline, it has no reverse operation.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0022_directory_member_search.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The migration that installed the latest frozen file on main (0021),
        # so the files apply in order.
        ("stewardship_reports", "0004_slim_directory_capture"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
