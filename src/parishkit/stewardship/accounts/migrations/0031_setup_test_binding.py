"""Forward migration: each setup test is bound to its own inputs (#1578).

Its SQL is the frozen file ``schema/migrations/0168_setup_test_binding.sql``
(prefix 0168, assigned in the repository-wide file sequence), read whole and
never parsed. It replaces ``stewardship_setup_mail_live_v1`` and
``stewardship_setup_slack_live_v1`` without their joins on a succeeded
parish data load and on the exact draft version, and
``stewardship_setup_readiness_guard_v1`` with the email and Slack test
checks bound to the staged credential and the settings sealed with it
instead of the frozen draft version and candidate digest. It ends with a DO
block that refuses to commit unless all three are installed as declared.
No model changes, so there is no state operation. Reversing needs its own
forward migration, so, like the baseline, it has no reverse operation.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0168_setup_test_binding.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The accounts app's previous migration.
        ("stewardship_accounts", "0030_secret_request_session"),
        # The migration that installs the previous frozen file on main (0132,
        # the refresh checks), so the files apply in prefix order. 0133-0166
        # are assigned to other open changes; whichever merges last
        # re-points this.
        ("stewardship_workflows", "0004_refresh_checks"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
