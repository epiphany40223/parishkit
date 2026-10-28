"""Forward migration: report totals count responses entered after close (#2298).

Its SQL is the frozen file ``schema/migrations/0192_late_report_totals.sql``
(prefix 0192, assigned in the repository-wide file sequence), read whole
and never parsed. It creates nothing and changes no table: it replaces the
daily fact set's guard, so a generation's dates run through its
``through_date`` with no cap at the campaign's end date, and the exact
export request and report email preparation guards, so their recorded
inputs run to the campaign's report end (the later of the end date and the
day of the newest live response under the recorded watermark) rather than
to the end date. It ends with a DO block that refuses to commit unless all
of it is installed as declared. Reversing needs its own forward migration.

Numbering: frozen 0192 follows 0191 (Ministry leader emails in the
settlement, #2300, jobs 0040), the previous file in the repository-wide
sequence, so the files apply in prefix order.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0192_late_report_totals.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The reports app's previous migration (frozen 0185, #2202), so the
        # app keeps one leaf.
        ("stewardship_reports", "0052_series_in_force"),
        # The migration that installs the previous frozen file (0191), so
        # the files apply in prefix order.
        ("stewardship_jobs", "0040_settle_leader_emails"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
