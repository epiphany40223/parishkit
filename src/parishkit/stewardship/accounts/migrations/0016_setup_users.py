"""Forward migration: first-time setup writes users (#993, a #952 follow-up).

Its SQL is the frozen file ``schema/migrations/0055_setup_users.sql``, read
whole and never parsed. It admits the users bootstrap
(``bootstrap-users-v10``), which ``bootstrap --admin-email`` now writes, and
its setup request format (``initial-setup-users-v10``), whose candidate is
``users-v10``; it teaches every bootstrap and setup guard both forms. It
ends with a DO block that refuses to commit unless all of it is installed
as declared.

The state operations record the same constraint changes for Django.
Nothing existing changes; reversing needs its own forward migration, so,
like the baseline, it has no reverse operation.
"""

from pathlib import Path

from django.db import migrations, models

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0055_setup_users.sql"
)

VALIDATION_SCHEMAS = [
    "parish-integrations-v1",
    "foundation-policy-v2",
    "campaign-foundation-v3",
    "bootstrap-policy-v1",
    "ministry-activity-v4",
    "campaign-content-v5",
    "source-cadence-v8",
    "users-v10",
    "bootstrap-users-v10",
]
REQUEST_SCHEMAS = [
    "parish-integrations-patch-v1",
    "foundation-policy-patch-v2",
    "operator-recovery-patch-v1",
    "operator-recovery-patch-v2",
    "operator-recovery-bootstrap-v1",
    "campaign-foundation-patch-v3",
    "ministry-activity-patch-v4",
    "operator-recovery-ministry-v4",
    "campaign-content-patch-v5",
    "operator-recovery-content-v5",
    "integration-credential-patch-v6",
    "initial-setup-patch-v7",
    "source-cadence-patch-v8",
    "operator-recovery-cadence-v8",
    "integration-credential-cadence-v8",
    "chair-seed-patch-v9",
    "operator-upgrade-users-v10",
    "users-patch-v10",
    "integration-credential-users-v10",
    "operator-recovery-users-v10",
    "initial-setup-users-v10",
]


class Migration(migrations.Migration):
    dependencies = [
        # Its own app's latest migration, which also installed the previous
        # frozen file (0054, #952 slice 6), so the files apply in prefix order.
        ("stewardship_accounts", "0015_leaders_without_user"),
    ]

    operations = [
        migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8")),
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveConstraint(
                    model_name="appliedconfigurationversion",
                    name="configuration_validation_schema",
                ),
                migrations.RemoveConstraint(
                    model_name="configurationchangerequest",
                    name="config_request_schema",
                ),
                migrations.AddConstraint(
                    model_name="appliedconfigurationversion",
                    constraint=models.CheckConstraint(
                        condition=models.Q(
                            ("validation_schema__in", VALIDATION_SCHEMAS)
                        ),
                        name="configuration_validation_schema",
                    ),
                ),
                migrations.AddConstraint(
                    model_name="configurationchangerequest",
                    constraint=models.CheckConstraint(
                        condition=models.Q(("request_schema__in", REQUEST_SCHEMAS)),
                        name="config_request_schema",
                    ),
                ),
            ]
        ),
    ]
