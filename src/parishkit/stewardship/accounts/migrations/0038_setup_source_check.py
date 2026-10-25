"""Forward migration: the setup wizard's quick parish data check (#2201).

Its SQL is the frozen file ``schema/migrations/0183_setup_source_check.sql``
(prefix 0183 in the repository-wide file sequence), read whole and never
parsed. It creates the setup parish data check table with its guard and its
end-of-setup discard, a trigger that lets a setup attempt freeze only with
its current check, binds the setup readiness record to a check instead of
a staged load (a new ``source_check_id`` column, ``source_result_id``
nullable, exactly one set), and replaces the readiness guard, the setup
attempt guard and the credential installers' readiness check accordingly. It
ends with a DO block that refuses to commit unless all of it is installed as
declared. The state operations record the model changes in Django's model
state, so ``makemigrations --check`` stays clean. Reversing needs its own
forward migration.

It follows the users swap (#952), so its table is granted only once it is
applied (runtime_grants.CLOSURE_TABLES).

Numbering: accounts 0037 is the definer operators (#2168, frozen 0179, on
main). Frozen files 0180 (the review wizard's undo, installed by workflows
0009) and 0181 (the reviewer's share methods, installed by reports 0049)
are on main; 0182 is reserved by another open pull request. The final chain
is fixed at train time, when this migration's reports dependency moves to
the migration that installs 0182 (reports 0050), so the files apply in
prefix order.
"""

import uuid
from pathlib import Path

import django.db.models.deletion
import django.db.models.functions.datetime
from django.db import migrations, models

import parishkit.stewardship.observability
import parishkit.stewardship.storage

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0183_setup_source_check.sql"
)
CHECK_ERRORS = [
    "parishsoft_key_refused",
    "parishsoft_organization_not_reached",
    "parishsoft_no_families",
    "parishsoft_check_unreadable",
    "parishsoft_unavailable",
]


class Migration(migrations.Migration):
    dependencies = [
        # The accounts app's previous migration (0179, the definer operators,
        # #2168).
        ("stewardship_accounts", "0037_definer_operators"),
        # The migration that installs the previous frozen file on main (0181,
        # the reviewer's share methods), so the files apply in prefix order.
        # At train time this moves to the one that installs 0182 (reports
        # 0050).
        ("stewardship_reports", "0049_pledge_share_methods"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))
            ],
            state_operations=[
                migrations.AlterField(
                    model_name="setupreadinessbinding",
                    name="source_result",
                    field=models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        to="stewardship_accounts.setupsourceresult",
                    ),
                ),
                migrations.CreateModel(
                    name="SetupSourceCheck",
                    fields=[
                        (
                            "id",
                            models.UUIDField(
                                default=uuid.uuid4,
                                editable=False,
                                primary_key=True,
                                serialize=False,
                            ),
                        ),
                        (
                            "created_at",
                            parishkit.stewardship.storage.UTCDateTimeField(
                                db_default=django.db.models.functions.datetime.Now(),
                                editable=False,
                            ),
                        ),
                        (
                            "actor_id",
                            models.UUIDField(blank=True, editable=False, null=True),
                        ),
                        (
                            "correlation_id",
                            models.UUIDField(
                                db_index=True,
                                default=parishkit.stewardship.observability.current_correlation,
                                editable=False,
                            ),
                        ),
                        (
                            "updated_at",
                            parishkit.stewardship.storage.UTCDateTimeField(
                                db_default=django.db.models.functions.datetime.Now(),
                                editable=False,
                            ),
                        ),
                        (
                            "version",
                            models.PositiveBigIntegerField(default=1, editable=False),
                        ),
                        ("credential_version", models.PositiveBigIntegerField()),
                        ("fingerprint", models.CharField(max_length=64)),
                        ("organization_id", models.PositiveBigIntegerField()),
                        ("state", models.CharField(default="queued", max_length=16)),
                        ("error_code", models.CharField(max_length=48, null=True)),
                        (
                            "organization_name",
                            models.CharField(max_length=200, null=True),
                        ),
                        ("family_count", models.PositiveBigIntegerField(null=True)),
                        ("member_count", models.PositiveBigIntegerField(null=True)),
                        ("worker_id", models.UUIDField(null=True)),
                        (
                            "started_at",
                            parishkit.stewardship.storage.UTCDateTimeField(null=True),
                        ),
                        (
                            "deadline_at",
                            parishkit.stewardship.storage.UTCDateTimeField(null=True),
                        ),
                        (
                            "finished_at",
                            parishkit.stewardship.storage.UTCDateTimeField(null=True),
                        ),
                        (
                            "attempt",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.PROTECT,
                                to="stewardship_accounts.setupattempt",
                            ),
                        ),
                        (
                            "credential",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.PROTECT,
                                to="stewardship_accounts.setupsealedcredential",
                            ),
                        ),
                    ],
                    options={
                        "db_table": "stewardship_setup_source_check",
                        "abstract": False,
                        "constraints": [
                            models.CheckConstraint(
                                condition=models.Q(("version__gte", 1)),
                                name="stewardship_accounts_setupsourcecheck_positive_version",
                            ),
                            models.UniqueConstraint(
                                condition=models.Q(
                                    ("state__in", ["queued", "running"])
                                ),
                                fields=("attempt",),
                                name="setup_check_one_waiting",
                            ),
                            models.CheckConstraint(
                                condition=models.Q(
                                    (
                                        "state__in",
                                        [
                                            "queued",
                                            "running",
                                            "ready",
                                            "failed",
                                            "discarded",
                                        ],
                                    )
                                ),
                                name="setup_check_known_state",
                            ),
                            models.CheckConstraint(
                                condition=models.Q(
                                    ("credential_version__gte", 1),
                                    ("fingerprint__regex", "^[0-9a-f]{64}$"),
                                    ("organization_id__gte", 1),
                                    ("organization_id__lt", 2147483648),
                                ),
                                name="setup_check_binding_shape",
                            ),
                            models.CheckConstraint(
                                condition=models.Q(
                                    models.Q(
                                        models.Q(("state", "failed"), _negated=True),
                                        ("error_code__isnull", True),
                                    ),
                                    models.Q(
                                        ("error_code__in", CHECK_ERRORS),
                                        ("state", "failed"),
                                    ),
                                    _connector="OR",
                                ),
                                name="setup_check_error_shape",
                            ),
                            models.CheckConstraint(
                                condition=models.Q(
                                    models.Q(
                                        ("error_code__isnull", True),
                                        ("family_count__gte", 1),
                                        ("family_count__lte", 2147483647),
                                        ("finished_at__isnull", False),
                                        ("member_count__gte", 0),
                                        ("member_count__lte", 2147483647),
                                        ("state", "ready"),
                                    ),
                                    models.Q(
                                        models.Q(("state", "ready"), _negated=True),
                                        ("family_count__isnull", True),
                                        ("member_count__isnull", True),
                                        ("organization_name__isnull", True),
                                    ),
                                    _connector="OR",
                                ),
                                name="setup_check_result_shape",
                            ),
                        ],
                    },
                ),
                migrations.AddField(
                    model_name="setupreadinessbinding",
                    name="source_check",
                    field=models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        to="stewardship_accounts.setupsourcecheck",
                    ),
                ),
                migrations.AddConstraint(
                    model_name="setupreadinessbinding",
                    constraint=models.CheckConstraint(
                        condition=models.Q(
                            models.Q(
                                ("source_check__isnull", False),
                                ("source_result__isnull", True),
                            ),
                            models.Q(
                                ("source_check__isnull", True),
                                ("source_result__isnull", False),
                            ),
                            _connector="OR",
                        ),
                        name="setup_readiness_one_source",
                    ),
                ),
            ],
        )
    ]
