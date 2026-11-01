"""Forward migration: report emails count today's campaign Families (#2331).

Its SQL is the frozen file ``schema/migrations/0196_report_email_population.sql``
(prefix 0196, assigned in the repository-wide file sequence), read whole
and never parsed. It changes no table. It creates
``stewardship_report_email_requests_v2``, the open additional information
requests without the Ignored ones, each with its stage, and replaces in
place the report snapshot guard (the kept requests are v2's and the pinned
generation is the current one), the fact runtime binding (a report email
capture builds its recorded inputs in either scope), and the source pin
guard and fact input pin commit check (a current generation's source pin
may belong to a report email capture). It ends with a DO block that refuses
to commit unless all of it is installed as declared. Reversing needs its
own forward migration.

Numbering: frozen 0196 follows 0192 (late report totals, #2298, reports
0053), the newest file on main when it was written; 0193 to 0195 were
assigned to other work. If the files between land first, only this
module's dependency and the file's number change.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0196_report_email_population.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The reports app's previous migration, which also installs the
        # previous frozen file (0192), so the app keeps one leaf and the
        # files apply in prefix order.
        ("stewardship_reports", "0053_late_report_totals"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
