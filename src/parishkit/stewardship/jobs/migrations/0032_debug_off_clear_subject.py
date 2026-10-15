"""Forward migration: the operator's debug-off clear names the switch (#1229).

Its SQL is the frozen file ``schema/migrations/0098_debug_off_clear_subject.sql``
(prefix 0098 in the repository-wide file sequence), read whole and never
parsed. It drops 0078's ``stewardship_debug_off_clear_v1()`` (which returned
only a row count) and creates ``stewardship_debug_off_clear_v2()``, the same
clear returning the cleared switch's id or NULL, so ``pk-stewardship
debug-off-clear`` records ``debug_logging_allowed`` naming the switch. It ends
with a DO block that refuses to commit unless v1 is gone and v2 is installed
as declared. No model changes, so there are no state operations. Reversing
needs its own forward migration.

It follows the users swap (#952) through 0028, so `migrate
--users-upgrade-base` never applies it and v2 is granted only once it is
applied (runtime_grants.CLOSURE_FUNCTIONS).
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0098_debug_off_clear_subject.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_jobs", "0031_debug_allow"),
        # The migration that installed the previous frozen file (0097, the
        # resume sender check), so the files apply in prefix order.
        ("stewardship_campaigns", "0011_resume_sender_check"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
