"""Forward migration: the read of the report sends that wait (#1872).

Its SQL is the frozen file
``schema/migrations/0165_report_waiting_read.sql``, read whole and never
parsed. It creates the Administrator-only definer read
``stewardship_report_email_waiting_v1``, granted to web, which lists every
report send waiting on an earlier message, with the messages it waits on. It
is a file of its own so the users swap can install it alone
(``runtime_grants.CLOSURE_FUNCTIONS``). It ends with a DO block that refuses
to commit unless the read is installed as declared. No model changes.
Reversing needs its own forward migration.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0165_report_waiting_read.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The coverage decisions (0164), whose comparison it reads.
        ("stewardship_reports", "0036_report_coverage_decisions"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
