"""Forward migration: Staff can always enter a response through Open form (#2294).

Its SQL is the frozen file ``schema/migrations/0188_staff_open_after_close.sql``
(prefix 0188 in the repository-wide file sequence), read whole and never
parsed. It adds the nullable ``assisted_by_id`` column to
``stewardship_family_session`` (the portal user whose Open form hand-off
started the session; NULL for every Family's own sign-in), creates the
hand-off's SECURITY DEFINER sign-in ``stewardship_family_assisted_login_v1``,
the only writer of that column, and replaces the Family session's
immutability guard, the form and submit guards and the receipt guard so a
session Open form started is also admitted after the campaign closes, and
the close's credential effects so that session is not revoked at the close. It
ends with a DO block that refuses to commit unless all of it is installed as
declared. The state operation records the column in Django's model state, so
``makemigrations --check`` stays clean. Reversing needs its own forward
migration.

It follows the users swap (#952), so its granted definer is granted only
once it is applied (runtime_grants.CLOSURE_FUNCTIONS).

Numbering: frozen 0188 follows 0187, which the coordinator assigned to
another change; until that lands this depends on accounts 0039 (frozen
0186), and the coordinator moves the dependency to 0187's migration when the
two board the same train.
"""

from pathlib import Path

from django.db import migrations, models

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0188_staff_open_after_close.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The campaigns app's previous migration (frozen 0175, #1393).
        ("stewardship_campaigns", "0019_campaign_families"),
        # The migration that installs the previous frozen file on main
        # (0186, the setup Off-site backups step, #2200), so the files apply
        # in prefix order.
        ("stewardship_accounts", "0039_setup_backup_step"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))
            ],
            state_operations=[
                migrations.AddField(
                    model_name="familysession",
                    name="assisted_by_id",
                    field=models.UUIDField(null=True),
                )
            ],
        )
    ]
