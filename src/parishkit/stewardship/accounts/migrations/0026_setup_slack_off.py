"""Forward migration: turning Slack off during setup discards its staged token.

Its SQL is the frozen file ``schema/migrations/0111_setup_slack_off.sql``
(prefix 0111, assigned in the repository-wide file sequence), read whole and
never parsed (#1499). It replaces ``stewardship_setup_secret_guard_v1`` so
the web may scrub a still-collecting attempt's staged Slack token, for the
attempt's owner, once the attempt's saved Slack step is off, and it turns
the one-row-per-attempt-and-target constraint into a partial unique index
over unscrubbed rows, so a new token can be staged beside the scrubbed
tombstone. The state operation records that uniqueness in the model state,
so ``makemigrations --check`` stays clean. It ends with a DO block that
refuses to commit unless both are installed as declared. Reversing needs
its own forward migration, so, like the baseline, it has no reverse
operation.
"""

from pathlib import Path

from django.db import migrations, models

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0111_setup_slack_off.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The accounts app's previous migration.
        ("stewardship_accounts", "0025_campaign_setup_review"),
        # The migration that installs the previous frozen file (0110,
        # Financial stewardship local days, #558), so the files apply in
        # prefix order. It follows the users swap, so
        # `migrate --users-upgrade-base` never applies it.
        ("stewardship_reports", "0030_financial_local_days"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))
            ],
            state_operations=[
                migrations.RemoveConstraint(
                    model_name="setupsealedcredential",
                    name="setup_secret_attempt_target",
                ),
                migrations.AddConstraint(
                    model_name="setupsealedcredential",
                    constraint=models.UniqueConstraint(
                        condition=models.Q(scrubbed_at=None),
                        fields=("attempt", "target"),
                        name="setup_secret_one_live_target",
                    ),
                ),
            ],
        )
    ]
