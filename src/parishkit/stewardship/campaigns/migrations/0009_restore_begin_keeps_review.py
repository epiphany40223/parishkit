"""Forward migration: restore-begin keeps an open review of the same backup (#799).

Its SQL is the frozen file named in ``FROZEN_SQL``, read whole and never
parsed. It replaces ``stewardship_runtime_transition_v1`` so a
``restore_begin`` is refused while a review is open for the same backup
time: a re-run would replace the review's restore id and move its held-email
cutoff. A different backup still starts a new review. It ends with a DO block
that refuses to commit unless the new guard is installed. No model changes,
so there is no state operation. Reversing needs its own forward migration,
so, like the baseline, it has no reverse operation.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0046_restore_begin_keeps_review.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_campaigns", "0008_workgroup_recovery_skip"),
        # The migration that installed the previous frozen file (#964), so
        # the files apply in prefix order.
        ("stewardship_reports", "0009_report_duid_sorts"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
