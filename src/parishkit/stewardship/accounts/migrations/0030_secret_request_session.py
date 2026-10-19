"""Forward migration: a secret request names its automation session (#1763).

Its SQL is the frozen file ``schema/migrations/0125_secret_request_session.sql``
(prefix 0125, provisional until the repository-wide file sequence assigns
it), read whole and never parsed. It adds the nullable
``stewardship_secret_request.automation_session_id`` (the automation session
a command-line request was made through; NULL for a browser request and for
every existing row), the definer check
``stewardship_automation_request_live_v1`` that the intake and
staged-to-testing guards now call for a request that names a session, and
drops ``stewardship_automation_instant_ended_v1`` with its grants, which
refused a browser's own key replacements once every automation session it
approved had ended. It changes no row, so no Admin session or Family
credential ends, and it can run again (the users swap's tests rerun it). It
ends with a DO block that refuses to commit unless all of it is installed as
declared. The state operation records the column in Django's model state, so
``makemigrations --check`` stays clean. Reversing needs its own forward
migration, so, like the baseline, it has no reverse operation.
"""

from pathlib import Path

from django.db import migrations, models

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0125_secret_request_session.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The migration that installs the previous frozen file (0124, #1762),
        # so the files apply in prefix order. It follows the users swap, so
        # `migrate --users-upgrade-base` never applies this file.
        ("stewardship_accounts", "0029_guard_wording"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))
            ],
            state_operations=[
                migrations.AddField(
                    model_name="secretreplacementrequest",
                    name="automation_session_id",
                    field=models.UUIDField(blank=True, null=True),
                )
            ],
        )
    ]
