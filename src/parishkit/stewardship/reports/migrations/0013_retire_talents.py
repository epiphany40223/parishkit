"""Forward migration: the retired Talents mechanism leaves the database (#970).

Its SQL is the frozen file ``schema/migrations/0056_retire_talents.sql``
(prefix 0056, assigned in the repository-wide file sequence), read whole and
never parsed. It replaces ``stewardship_service_answers_guard_v1`` so each
new Member entry's talents map must be empty, drops
``stewardship_talent_defaults_v1`` (only that guard called it), adds
``stewardship_limitations_report_v1`` (the Limitations report without
talents) and drops ``stewardship_talent_report_v1``. Stored submissions and
campaign configurations are untouched. It ends with a DO block that refuses
to commit unless all of that is installed as declared and the guard keeps
its grants. No model changes, so there is no state operation. Reversing
needs its own forward migration, so, like the baseline, it has no reverse
operation.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0056_retire_talents.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The reports app's previous migration (it installed 0050, #933).
        ("stewardship_reports", "0012_directory_response_filters"),
        # The migration that installed the previous frozen file (0055,
        # #993), so the files apply in prefix order. It follows the users
        # swap, so `migrate --users-upgrade-base` never applies this file.
        ("stewardship_accounts", "0016_setup_users"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
