"""Forward migration: an ordinary pause resumes without a sender check (#1288).

Its SQL is the frozen file ``schema/migrations/0097_resume_sender_check.sql``
(prefix 0097, assigned in the repository-wide file sequence), read whole and
never parsed. It replaces the ``stewardship_delivery_control_health`` view so
a sender check is required only after real sender or delivery trouble, and a
required check's proof lasts 30 minutes instead of five. The column list,
owner and grants are unchanged, and the file ends with a DO block that
refuses to commit unless the new view is installed with its grants intact.
No model changes, so there is no state operation. Reversing needs its own
forward migration, so, like the baseline, it has no reverse operation.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0097_resume_sender_check.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_campaigns", "0010_restore_holds_after_release"),
        # The migration that installed the previous frozen file (0096,
        # #528), so the files apply in prefix order.
        ("stewardship_responses", "0006_proposal_review"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
