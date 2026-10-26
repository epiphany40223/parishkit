"""Forward migration: Financial stewardship shows the values in force (#2196).

Its SQL is the frozen file ``schema/migrations/0182_financial_in_force.sql``
(prefix 0182, assigned in the repository-wide file sequence), read whole
and never parsed. It replaces 0175's ``stewardship_financial_report_v1`` in
place, so the report, its exports and every reader of its selection use a
reviewer's Edit of the annual pledge, frequency and share methods while it
is in force, else the Family's own answer, and each row says whether an
Edit is in force with the Family's own answer beside it. It ends with a DO
block that refuses to commit unless the body is installed as declared and
the selection runs. No model state changes. Reversing needs its own forward
migration.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0182_financial_in_force.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # 0181's (the reviewer's share methods, #2177): the reports app's
        # previous migration, so the app keeps one leaf, and the one that
        # installs the previous frozen file, so the files apply in prefix
        # order. The selection reads its reviewed_shares column.
        ("stewardship_reports", "0049_pledge_share_methods"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
