"""Forward migration: the campaign statistics count the values in force (#2202).

Its SQL is the frozen file ``schema/migrations/0184_statistics_in_force.sql``
(prefix 0184, assigned in the repository-wide file sequence), read whole
and never parsed. It adds the SECURITY DEFINER
``stewardship_pledge_edits_in_force_v1``: for each Family with a pledge Edit
in force (found exactly as 0182's Financial selection finds it), the
response it is in force on and the Edit's annual pledge, so the statistics
capture, which the general worker also runs without any grant on answers
or pledge reviews, counts the values in force. It ends with a DO block that
refuses to commit unless the definer is installed as declared and its
selection runs. No model state changes. Reversing needs its own forward
migration.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0184_statistics_in_force.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # 0182's (Financial stewardship in force, #2196): the reports app's
        # previous migration, so the app keeps one leaf, and the one that
        # installs the previous frozen file, so the files apply in prefix
        # order.
        ("stewardship_reports", "0050_financial_in_force"),
        # 0183's (the setup parish data check, #2201): the accounts app's
        # previous migration. Frozen file 0184 follows file 0183, so the
        # files apply in prefix order across the two apps.
        ("stewardship_accounts", "0038_setup_source_check"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
