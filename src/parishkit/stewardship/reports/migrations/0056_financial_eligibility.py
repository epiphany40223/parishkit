"""Forward migration: report Family status is the dashboard's eligibility (#2335).

Its SQL is the frozen file ``schema/migrations/0197_financial_eligibility.sql``
(prefix 0197, assigned in the repository-wide file sequence), read whole
and never parsed. It replaces 0194's ``stewardship_financial_report_v1``,
``stewardship_information_report_v1`` and ``stewardship_information_report_v2``
in place, changing only each one's ``active`` column: each row's
``portal_eligible``, the eligibility the Response dashboard counts, under
every Families rule. Under the legacy rule that adds "registered at the
parish" to ParishSoft's active flag, so Financial stewardship's "In this
campaign today", its ParishSoft status badge and "No longer in this
campaign" refinement, and Additional information's badge agree with the
dashboard. It ends with a DO block that refuses to commit unless the bodies
are installed as declared and the selections run. No model state changes.
Reversing needs its own forward migration.

Numbering: frozen 0197 follows 0196 (report email population, #2331,
reports 0055), the previous file in the repository-wide sequence, so the
files apply in prefix order.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0197_financial_eligibility.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The reports app's previous migration, which installs the previous
        # frozen file (0196, #2331), so the app keeps one leaf and the files
        # apply in prefix order. It follows reports 0054 (0194, #2320, whose
        # bodies this replaces) and jobs 0042 (0195).
        ("stewardship_reports", "0055_report_email_population"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
