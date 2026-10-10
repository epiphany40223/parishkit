"""Forward migration: the users form of the sign-in policy (#952, slice 1).

Its SQL is the frozen file ``schema/migrations/0047_portal_users.sql``, read
whole and never parsed. It adds a Name and one role to the address-rule
projection (a user), makes the older rules' origin columns nullable, admits
the ``users-v10`` document schema and the ``operator-upgrade-users-v10``
request format with its ``operator_upgrade`` authority, teaches the policy
and section projection checks the users form, and adds the activation guard
that lets only the offline upgrade convert (keeping every Administrator)
and the two definer reads that conversion needs. It ends with a DO block
that refuses to commit unless all of it is installed as declared.

The state operations record the same model changes for Django. Nothing is
converted here; that is the offline ``upgrade-users`` command. Reversing
needs its own forward migration, so, like the baseline, it has no reverse
operation.
"""

from pathlib import Path

from django.db import migrations, models

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0047_portal_users.sql"
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
]
RECOVERY_SCHEMAS = [
    "operator-recovery-patch-v1",
    "operator-recovery-patch-v2",
    "operator-recovery-bootstrap-v1",
    "operator-recovery-ministry-v4",
    "operator-recovery-content-v5",
    "operator-recovery-cadence-v8",
]


def _user_shape(role, roles):
    """One users-form row: a Name, this role, its cumulative roles."""
    return models.Q(
        ("creation_operation__isnull", True),
        ("creation_origin__isnull", True),
        ("name__isnull", False),
        ("role", role),
        ("roles", roles),
    )


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_accounts", "0010_ministry_leaders_from_roles"),
        # The migration that installed the previous frozen file (0046, #799),
        # so the files apply in prefix order.
        ("stewardship_campaigns", "0009_restore_begin_keeps_review"),
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
                migrations.RemoveConstraint(
                    model_name="configurationchangerequest",
                    name="config_request_actor",
                ),
                migrations.AddField(
                    model_name="addressrule",
                    name="name",
                    field=models.CharField(max_length=200, null=True),
                ),
                migrations.AddField(
                    model_name="addressrule",
                    name="role",
                    field=models.CharField(max_length=32, null=True),
                ),
                migrations.AlterField(
                    model_name="addressrule",
                    name="creation_operation",
                    field=models.UUIDField(null=True),
                ),
                migrations.AlterField(
                    model_name="addressrule",
                    name="creation_origin",
                    field=models.CharField(max_length=16, null=True),
                ),
                migrations.AddConstraint(
                    model_name="addressrule",
                    constraint=models.CheckConstraint(
                        condition=models.Q(
                            models.Q(
                                ("creation_operation__isnull", False),
                                ("creation_origin__isnull", False),
                                ("name__isnull", True),
                                ("role__isnull", True),
                            ),
                            _user_shape("ministry_leader", ["ministry_leader"]),
                            _user_shape("staff", ["ministry_leader", "staff"]),
                            _user_shape(
                                "administrator",
                                ["administrator", "ministry_leader", "staff"],
                            ),
                            _connector="OR",
                        ),
                        name="address_rule_shape",
                    ),
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
                migrations.AddConstraint(
                    model_name="configurationchangerequest",
                    constraint=models.UniqueConstraint(
                        condition=models.Q(("authority", "operator_upgrade")),
                        fields=("request_key",),
                        name="operator_upgrade_operation",
                    ),
                ),
            ]
        ),
    ]
