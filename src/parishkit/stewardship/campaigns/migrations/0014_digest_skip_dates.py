"""Forward migration: skip one send of an Admin digest (#1658).

Its SQL is the frozen file ``schema/migrations/0130_digest_skip_dates.sql``
(prefix 0130, provisional until the coordinator assigns it at merge, in the
repository-wide file sequence), read whole and never parsed. It replaces
``stewardship_catchup_due_v1`` (the database's canonical digest slot check)
so a date in the current revision's ``skip_dates`` is no slot, and
``stewardship_weekly_digest_page_v1`` so a weekly page accepts a skipped week
without an occurrence. It ends with a DO block that refuses to commit unless
both are installed as declared with their grants, owner and settings
unchanged. No model changes (the skip dates live in the revision's existing
``values``), so there is no state operation. Reversing needs its own forward
migration, so, like the baseline, it has no reverse operation.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0130_digest_skip_dates.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_campaigns", "0013_any_creation_timezone"),
        # The migration that installs the previous frozen file (0116, #1562),
        # so the files apply in prefix order.
        ("stewardship_jobs", "0034_sent_mail_recipients"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
