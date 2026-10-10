"""Forward migration: each task type has its allowed creator logins (#389).

Its SQL is the frozen file ``schema/migrations/0041_task_type_creators.sql``,
read whole and never parsed. It adds ``stewardship_task_type_creators_v1``
(the logins whose code creates each task type) and replaces
``stewardship_task_login_guard_v1`` so that an INSERT of a known type from any
other non-owner login is refused. It ends with a DO block that refuses to
commit unless both are installed as declared, every known type has a creator
and the guard is still attached. No model changes, so there is no state
operation. Reversing needs its own forward migration, so, like the baseline,
it has no reverse operation.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0041_task_type_creators.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The jobs app's previous migration.
        ("stewardship_jobs", "0019_admin_command_failure_log"),
        # The migration that installed the previous frozen file (0040,
        # #389 L3), so the files apply in prefix order.
        ("stewardship_reports", "0008_ministry_report_actor_scope"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
