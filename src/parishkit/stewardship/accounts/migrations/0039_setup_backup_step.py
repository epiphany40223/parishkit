"""Forward migration: the setup wizard's optional Off-site backups step (#2200).

Its SQL is the frozen file ``schema/migrations/0186_setup_backup_step.sql``
(prefix 0186 in the repository-wide file sequence), read whole and never
parsed. It lets the setup draft hold a ``backup`` section (the
``setup_public_step`` CHECK and the draft guard's declared fields), and
creates the worker's definer ``stewardship_setup_backup_check_v1()``, which
queues one off-site backup "Test access" check of the applied folder in the
transaction that writes the configured marker. It ends with a DO block that
refuses to commit unless all of it is installed as declared. The state
operations record the widened CHECK in Django's model state, so
``makemigrations --check`` stays clean. Reversing needs its own forward
migration.

It follows the users swap (#952), so its granted definer is granted only
once it is applied (runtime_grants.CLOSURE_FUNCTIONS).

Numbering: it follows accounts 0038 (the setup parish data check, frozen
0183, #2201) and reports 0052 (frozen 0185, #2202), so the files apply in
prefix order: frozen 0184 and 0185 are the reports migrations 0051 and 0052.
"""

from pathlib import Path

from django.db import migrations, models

# The draft section steps the CHECK admits, in its order: 0003's with
# backup after testing.
STEPS = [
    "parish",
    "branding",
    "access",
    "mail",
    "slack",
    "testing",
    "backup",
    "campaign",
    "schedules",
    "page_access_denied",
    "page_additional",
    "page_census",
    "page_closing",
    "page_financial",
    "page_login_help",
    "page_member_census",
    "page_ministry",
    "page_post_end",
    "page_pre_start",
    "page_review",
    "page_submission_confirmation",
    "page_thank_you",
    "page_welcome",
    "email_confirmation",
    "email_critical_alert",
    "email_daily_digest",
    "email_initial",
    "email_reminder",
    "email_weekly_digest",
]

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0186_setup_backup_step.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The accounts app's previous migration, which also installs the
        # previous frozen file on this branch (0183, #2201).
        ("stewardship_accounts", "0038_setup_source_check"),
        # The migration that installs frozen 0185, so the frozen files apply
        # in prefix order across apps.
        ("stewardship_reports", "0052_series_in_force"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))
            ],
            state_operations=[
                migrations.RemoveConstraint(
                    model_name="setupdraftsection",
                    name="setup_public_step",
                ),
                migrations.AddConstraint(
                    model_name="setupdraftsection",
                    constraint=models.CheckConstraint(
                        condition=models.Q(
                            (
                                "step__in",
                                STEPS,
                            )
                        ),
                        name="setup_public_step",
                    ),
                ),
            ],
        )
    ]
