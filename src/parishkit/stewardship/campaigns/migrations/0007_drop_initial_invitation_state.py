"""Forward migration: drop the unused Family invitation-state column (#472).

``stewardship_family_campaign.initial_invitation_state`` was never read or
written after it was created: it read "not_sent" for every Family, including
those whose invitation was delivered, while invitation state lives in the
schedule occurrences and outbox messages. The model no longer has the field.

The database side is the frozen file
``schema/migrations/0028_drop_initial_invitation_state.sql``, read whole and
never parsed: ``DROP COLUMN IF EXISTS`` (a fresh install's baseline already
lacks the column), then a DO block that refuses to commit unless the column
is gone. The state side removes the field from Django's model state. The
scripted upgrade stops every application service before migrating, so no
process of the previous release runs against the table without the column.
Reversing needs its own forward migration, so there is no reverse operation.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0028_drop_initial_invitation_state.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_campaigns", "0006_scheduler_link_preparation"),
        # The migration that installed the previous frozen file (0027), so
        # the files apply in prefix order.
        ("stewardship_accounts", "0007_recovery_session_grants"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))
            ],
            state_operations=[
                migrations.RemoveField(
                    model_name="familycampaign", name="initial_invitation_state"
                )
            ],
        )
    ]
