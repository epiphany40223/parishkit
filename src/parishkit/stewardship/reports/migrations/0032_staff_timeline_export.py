"""Forward migration: Staff may export a Family's Timeline (#1799).

Its SQL is the frozen file ``schema/migrations/0160_staff_timeline_export.sql``
(prefix 0160 in the repository-wide file sequence), read whole and never
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
    / "0160_staff_timeline_export.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The reports app's previous migration.
        ("stewardship_reports", "0031_leader_request_contacts"),
        # The migration that installs the previous frozen file (0120,
        # leader emails, #1592), so the files apply in prefix order.
        ("stewardship_jobs", "0035_leader_emails"),
    ]

    operations = [
        migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8")),
    ]
