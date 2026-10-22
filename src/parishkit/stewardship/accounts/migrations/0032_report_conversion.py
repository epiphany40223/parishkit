"""Model state for the report email conversion (#1786).

It admits the configuration schema ``campaign-series-v12`` and the
conversion's request format ``operator-upgrade-reports-v12``. The frozen file 0161
(``stewardship_reports.0041_report_conversion``) changes the constraints
in the database; this records them in Django's model state only, so
``makemigrations --check`` stays clean.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_reports", "0041_report_conversion"),
        ("stewardship_accounts", "0031_person_identity_reads"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveConstraint(
                    model_name="appliedconfigurationversion",
                    name="configuration_validation_schema",
                ),
                migrations.RemoveConstraint(
                    model_name="configurationchangerequest",
                    name="config_request_actor",
                ),
                migrations.RemoveConstraint(
                    model_name="configurationchangerequest",
                    name="config_request_schema",
                ),
                migrations.AddConstraint(
                    model_name="appliedconfigurationversion",
                    constraint=models.CheckConstraint(
                        condition=models.Q(
                            (
                                "validation_schema__in",
                                [
                                    "parish-integrations-v1",
                                    "foundation-policy-v2",
                                    "campaign-foundation-v3",
                                    "bootstrap-policy-v1",
                                    "ministry-activity-v4",
                                    "campaign-content-v5",
                                    "source-cadence-v8",
                                    "users-v10",
                                    "bootstrap-users-v10",
                                    "campaign-series-v11",
                                    "campaign-series-v12",
                                ],
                            )
                        ),
                        name="configuration_validation_schema",
                    ),
                ),
                migrations.AddConstraint(
                    model_name="configurationchangerequest",
                    constraint=models.CheckConstraint(
                        condition=models.Q(
                            (
                                "request_schema__in",
                                [
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
                                    "operator-upgrade-reports-v12",
                                ],
                            )
                        ),
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
                                            "operator-recovery-patch-v1",
                                            "operator-recovery-patch-v2",
                                            "operator-recovery-bootstrap-v1",
                                            "operator-recovery-ministry-v4",
                                            "operator-recovery-content-v5",
                                            "operator-recovery-cadence-v8",
                                            "operator-recovery-users-v10",
                                            "operator-upgrade-users-v10",
                                            "operator-upgrade-reports-v12",
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
                                (
                                    "request_schema__in",
                                    [
                                        "operator-recovery-patch-v1",
                                        "operator-recovery-patch-v2",
                                        "operator-recovery-bootstrap-v1",
                                        "operator-recovery-ministry-v4",
                                        "operator-recovery-content-v5",
                                        "operator-recovery-cadence-v8",
                                        "operator-recovery-users-v10",
                                    ],
                                ),
                            ),
                            models.Q(
                                ("actor_id__isnull", True),
                                ("authority", "operator_upgrade"),
                                ("confirmed_deployment_id__isnull", False),
                                ("operator_name__isnull", False),
                                ("operator_reason__isnull", False),
                                ("recovery_target__isnull", True),
                                (
                                    "request_schema__in",
                                    [
                                        "operator-upgrade-users-v10",
                                        "operator-upgrade-reports-v12",
                                    ],
                                ),
                            ),
                            _connector="OR",
                        ),
                        name="config_request_actor",
                    ),
                ),
            ],
        )
    ]
