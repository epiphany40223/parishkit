"""Forward migration: privileged Admin actions accept a sign-in of any age.

Its SQL is the frozen file ``schema/migrations/0114_no_sign_in_age.sql``
(prefix 0114, assigned in the repository-wide file sequence), read whole and
never parsed (#1547). It replaces, in place, every guard that refused a row
unless the Admin had signed in with Google within the last five minutes (or
an hour, for setup credentials), or a live full-scope automation session
stood in for that sign-in. Each guard keeps every other clause: a live,
unrevoked, active session of the actor, bound to the recorded sign-in
instant where it was. It ends with a DO block that refuses to commit unless
every body is installed as declared with its rights, configuration and
grants unchanged. No model changes, so there is no state operation.
Reversing needs its own forward migration, so, like the baseline, it has no
reverse operation.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0114_no_sign_in_age.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The accounts app's previous migration (0111, setup can turn Slack
        # off, #1502), whose setup credential guard 0114 replaces again.
        ("stewardship_accounts", "0026_setup_slack_off"),
        # The migration that installs the previous frozen file (0113, the
        # census handling registry, #1596), so the files apply in prefix
        # order; it follows 0112's (#1524), whose publication confirmation
        # guard 0114 replaces again. It follows the users swap, so
        # `migrate --users-upgrade-base` never applies it.
        ("stewardship_responses", "0011_census_handling_registry"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
