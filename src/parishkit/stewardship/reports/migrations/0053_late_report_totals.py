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

Numbering: frozen 0192 follows 0188 (Staff Open form after close, #2294,
campaigns 0020), on which this change is stacked; 0187 and 0189 to 0191 are
assigned to other changes, and the coordinator moves the dependency to
0191's migration when they board the same train.
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
        # The migration that installs the previous frozen file in this
        # stack (0188, Staff Open form after close, #2294), so the files
        # apply in prefix order.
        ("stewardship_campaigns", "0020_staff_open_after_close"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
