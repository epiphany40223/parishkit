"""Forward migration: skip one send of an Admin digest (#1658).

Its SQL is the frozen file ``schema/migrations/0130_digest_skip_dates.sql``
(prefix 0130, provisional until the coordinator assigns it at merge, in the
repository-wide file sequence), read whole and never parsed. It replaces
three functions: ``stewardship_occurrence_guard_v1`` so no writer can
insert a Daily or Weekly digest occurrence for a slot in its revision's
``skip_dates`` (and repeats its ``ALTER ... SECURITY DEFINER``, which
``CREATE OR REPLACE`` resets); ``stewardship_catchup_due_v1`` (the
database's canonical digest slot check) so a skipped date is no slot; and
``stewardship_weekly_digest_page_v1`` so a weekly page accepts a skipped
week without an occurrence. It ends with a DO block that refuses to commit
unless all three are installed with exactly its bodies (each body's md5),
the guard is a definer again, and their grants, owner and settings are
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
