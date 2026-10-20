"""Forward migration: Staff may export a Family's Timeline (#1799).

Its SQL is the frozen file ``schema/migrations/0144_staff_timeline_export.sql``
(prefix 0144 in the repository-wide file sequence), read whole and never
parsed. It re-creates the timeline capture trigger, the export request guard
and the export request authorization from their latest bodies so that a
current Staff member, not only an Administrator, may capture and request a
``family_timeline`` export, keeps a Testing capture and regenerating another
requester's capture Administrator-only, and raises the capture's size bound
to 1 MiB. It ends with a DO block that refuses to commit unless all of it is
installed as declared. No model changes, so there are no state operations.
Reversing needs its own forward migration.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0144_staff_timeline_export.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The reports app's previous migration on main (0127's).
        ("stewardship_reports", "0032_pledge_review"),
        # Leader emails (0120, #1592), which this branch was written on.
        ("stewardship_jobs", "0035_leader_emails"),
        # The migration that installs main's last frozen file (0132), so the
        # files apply in prefix order. Its number and these dependencies
        # follow the R40 pipeline's (0133 to 0143) once that lands first.
        ("stewardship_workflows", "0004_refresh_checks"),
    ]

    operations = [
        migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8")),
    ]
