"""Forward migration: settle an unconfirmed Ministry leader email (#2300).

Its SQL is the frozen file ``schema/migrations/0191_settle_leader_emails.sql``
(prefix 0191 in the repository-wide file sequence), read whole and never
parsed. It replaces the delivery resolution guard in place so that a
Ministry leader email whose outcome is unknown can be marked delivered or
recorded as not sent, with a note, and never resent, stating SECURITY
DEFINER again. It ends with a DO block that refuses to commit unless the
guard is installed as declared. No model state changes. Reversing needs its
own forward migration.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0191_settle_leader_emails.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The previous frozen file (0190), whose guard body this replaces.
        ("stewardship_jobs", "0039_settle_notices"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
