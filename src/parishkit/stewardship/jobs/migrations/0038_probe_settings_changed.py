"""Forward migration: re-check a queued "Test access" check when it runs (#269).

Its SQL is the frozen file ``schema/migrations/0176_backup_probe_recheck.sql``
(prefix 0176 in the repository-wide file sequence), read whole and never
parsed. It creates the Google Workspace installer's definer
``stewardship_backup_destination_v1()``, which returns the applied backup
folder link and delegated mailbox user, and adds the ``settings_changed``
failure kind to the access check's ``backup_probe_failure_kind`` CHECK. It
ends with a DO block that refuses to commit unless both are installed as
declared. The model state records the widened CHECK. Reversing needs its own
forward migration.
"""

from pathlib import Path

from django.db import migrations, models

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0176_backup_probe_recheck.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The jobs app's previous migration, the report conversion's model
        # state (#1786).
        ("stewardship_jobs", "0037_report_conversion"),
        # The migration that installs the previous frozen file (0175, the
        # campaign's Families rule), so the files apply in prefix order. It
        # follows the users swap, so `migrate --users-upgrade-base` never
        # applies this file and its granted function is registered in
        # runtime_grants.CLOSURE_FUNCTIONS.
        ("stewardship_campaigns", "0019_campaign_families"),
        ("stewardship_reports", "0047_report_email_test"),
    ]

    operations = [
        migrations.RunSQL(
            FROZEN_SQL.read_text(encoding="utf-8"),
            state_operations=[
                migrations.RemoveConstraint(
                    model_name="backupdriveprobe",
                    name="backup_probe_failure_kind",
                ),
                migrations.AddConstraint(
                    model_name="backupdriveprobe",
                    constraint=models.CheckConstraint(
                        condition=models.Q(("failure_kind__isnull", True))
                        | models.Q(
                            (
                                "failure_kind__in",
                                (
                                    "authorization",
                                    "api_disabled",
                                    "not_found",
                                    "permission",
                                    "not_folder",
                                    "credential",
                                    "verification",
                                    "unavailable",
                                    "unexpected",
                                    "unanswered",
                                    "settings_changed",
                                ),
                            )
                        ),
                        name="backup_probe_failure_kind",
                    ),
                ),
            ],
        )
    ]
