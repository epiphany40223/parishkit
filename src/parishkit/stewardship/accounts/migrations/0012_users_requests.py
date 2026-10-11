"""Forward migration: the request formats of a users configuration (#952).

Its SQL is the frozen file ``schema/migrations/0051_users_requests.sql``,
read whole and never parsed. It admits the ordinary Admin edit
(``users-patch-v10``), credential update (``integration-credential-users-v10``)
and offline Admin-access recovery (``operator-recovery-users-v10``) formats
of a configuration in the users form, and teaches the recovery request
trigger a users recovery. It ends with a DO block that refuses to commit
unless all of it is installed as declared.

The state operations record the same constraint changes for Django. Nothing
existing changes; reversing needs its own forward migration, so, like the
baseline, it has no reverse operation.
"""

from pathlib import Path

from django.db import migrations, models

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0051_users_requests.sql"
)

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
]
RECOVERY_SCHEMAS = [
    "operator-recovery-patch-v1",
    "operator-recovery-patch-v2",
    "operator-recovery-bootstrap-v1",
    "operator-recovery-ministry-v4",
    "operator-recovery-content-v5",
    "operator-recovery-cadence-v8",
    "operator-recovery-users-v10",
]


class Migration(migrations.Migration):
    dependencies = [
        # Its own app's latest migration.
        ("stewardship_accounts", "0011_portal_users"),
        # The migration that installed the previous frozen file (0050, #945),
        # so the files apply in prefix order.
        ("stewardship_reports", "0012_directory_response_filters"),
    ]

    operations = [
        migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8")),
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveConstraint(
                    model_name="configurationchangerequest",
                    name="config_request_schema",
                ),
                migrations.RemoveConstraint(
                    model_name="configurationchangerequest",
                    name="config_request_actor",
                ),
                migrations.AddConstraint(
                    model_name="configurationchangerequest",
                    constraint=models.CheckConstraint(
                        condition=models.Q(("request_schema__in", REQUEST_SCHEMAS)),
                        name="config_request_schema",
                    ),
                ),
                migrations.AddConstraint(
                    model_name="configurationchangerequest",
                    constraint=models.CheckConstraint(
                        condition=models.Q(
                            models.Q(
                                ("actor_id__isnull", False),
                                ("authority", "admin"),
                                ("confirmed_deployment_id__isnull", True),
                                ("operator_name__isnull", True),
                                ("operator_reason__isnull", True),
                                ("recovery_target__isnull", True),
                                models.Q(
                                    (
                                        "request_schema__in",
                                        [
                                            *RECOVERY_SCHEMAS,
                                            "operator-upgrade-users-v10",
                                        ],
                                    ),
                                    _negated=True,
                                ),
                            ),
                            models.Q(
                                ("actor_id__isnull", True),
                                ("authority", "operator_recovery"),
                                ("confirmed_deployment_id__isnull", False),
                                ("operator_name__isnull", False),
                                ("operator_reason__isnull", False),
                                ("recovery_target__isnull", False),
                                ("request_schema__in", RECOVERY_SCHEMAS),
                            ),
                            models.Q(
                                ("actor_id__isnull", True),
                                ("authority", "operator_upgrade"),
                                ("confirmed_deployment_id__isnull", False),
                                ("operator_name__isnull", False),
                                ("operator_reason__isnull", False),
                                ("recovery_target__isnull", True),
                                ("request_schema", "operator-upgrade-users-v10"),
                            ),
                            _connector="OR",
                        ),
                        name="config_request_actor",
                    ),
                ),
            ]
        ),
    ]
