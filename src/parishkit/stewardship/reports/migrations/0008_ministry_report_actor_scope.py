"""Forward migration: Ministry report scope comes from the actor in SQL (#389).

Its SQL is the frozen file ``schema/migrations/0040_ministry_report_actor_scope.sql``
(prefix 0040, assigned in the repository-wide file sequence), read whole and
never parsed. It adds ``stewardship_ministry_report_v2`` and
``stewardship_ministry_followup_v2``, which take the signed-in actor and derive
the operational flag and Ministry scope from ``stewardship_ministry_scope_v1``
before calling the unchanged v1 selections. It ends with a DO block that
refuses to commit unless both are installed with their declared attributes.
No model changes, so there is no state operation. Reversing needs its own
forward migration, so, like the baseline, it has no reverse operation.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0040_ministry_report_actor_scope.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The reports app's previous migration, which also installed the
        # previous frozen file (0039, #851), so the files apply in prefix
        # order.
        ("stewardship_reports", "0007_response_list_sort_audit"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
