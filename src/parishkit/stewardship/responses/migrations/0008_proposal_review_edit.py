"""Forward migration: an Administrator edits an Automatic change's value (#528).

Its SQL is the frozen file ``schema/migrations/0107_proposal_review_edit.sql``
(number 0107, assigned centrally), read whole and never parsed. It replaces
``stewardship_proposal_review_guard_v1`` (created by 0096), so a review is
either a decision or an edited value (ADM-09.04), adds the edit guard that
lets the web login change a proposal's edit fields only as a review row
records them, and ends with a DO block that refuses to commit unless both
are installed. It changes no table's columns, so there is no state
operation. Reversing needs its own forward
migration, so, like the baseline, it has no reverse operation.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0107_proposal_review_edit.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_responses", "0007_publication_tasks"),
        # The migration that installed the previous frozen file (0106, the
        # Admin input refusal log, in the audit app), so the files apply in
        # prefix order.
        ("stewardship_audit", "0004_admin_input_refusal_log"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
