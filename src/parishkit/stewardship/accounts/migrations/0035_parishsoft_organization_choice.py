"""Forward migration: the setup wizard's ParishSoft organization choice (#1554).

Its SQL is the frozen file
``schema/migrations/0174_parishsoft_organization_choice.sql`` (prefix 0174
in the repository-wide file sequence), read whole and never parsed. It
creates the setup ParishSoft organization lookup table with its guard, its
end-of-setup scrub and the setup load's organization check, and replaces
the setup credential guard (a ParishSoft key staged with no organization,
and its discard) and the audit safe context (the choice's four counts). It
ends with a DO block that refuses to commit unless all of it is installed
as declared. The state operation records the model in Django's model
state, so ``makemigrations --check`` stays clean. Reversing needs its own
forward migration.

It follows the users swap (#952), so its table is granted only once it is
applied (runtime_grants.CLOSURE_TABLES).
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
    / "0174_parishsoft_organization_choice.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The accounts app's previous migration (0168, the setup test
        # binding, #1578), so the app keeps one leaf.
        ("stewardship_accounts", "0034_setup_test_binding"),
        # The migration that installs the previous frozen file (0173, the
        # report email test, #2036), so the files apply in prefix order.
        ("stewardship_reports", "0047_report_email_test"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))
            ],
            state_operations=[
                migrations.CreateModel(
                    name="SetupParishSoftLookup",
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
                        ("state", models.CharField(default="queued", max_length=16)),
                        ("error_code", models.CharField(max_length=48, null=True)),
                        ("organizations", models.JSONField(null=True)),
                        ("chosen_id", models.PositiveBigIntegerField(null=True)),
                        ("chosen_name", models.CharField(max_length=200, null=True)),
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
                            "scrubbed_at",
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
                        "db_table": "stewardship_setup_parishsoft_lookup",
                        "abstract": False,
                        "constraints": [
                            models.CheckConstraint(
                                condition=models.Q(("version__gte", 1)),
                                name="stewardship_accounts_setupparishsoftlookup_positive_version",
                            ),
                            models.UniqueConstraint(
                                condition=models.Q(
                                    (
                                        "state__in",
                                        ["queued", "running", "ready", "failed"],
                                    )
                                ),
                                fields=("attempt",),
                                name="setup_lookup_one_open",
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
                                            "chosen",
                                            "discarded",
                                        ],
                                    )
                                ),
                                name="setup_lookup_known_state",
                            ),
                            models.CheckConstraint(
                                condition=models.Q(
                                    ("credential_version__gte", 1),
                                    ("fingerprint__regex", "^[0-9a-f]{64}$"),
                                ),
                                name="setup_lookup_binding_shape",
                            ),
                            models.CheckConstraint(
                                condition=models.Q(
                                    ("error_code__isnull", True),
                                    models.Q(
                                        (
                                            "error_code__in",
                                            [
                                                "parishsoft_key_refused",
                                                "parishsoft_no_organizations",
                                                "parishsoft_no_family_organizations",
                                                "parishsoft_organizations_unreadable",
                                                "parishsoft_unavailable",
                                            ],
                                        ),
                                        ("state", "failed"),
                                    ),
                                    _connector="OR",
                                ),
                                name="setup_lookup_error_shape",
                            ),
                            models.CheckConstraint(
                                condition=models.Q(
                                    ("organizations__isnull", True),
                                    ("state", "ready"),
                                    _connector="OR",
                                ),
                                name="setup_lookup_list_shape",
                            ),
                            models.CheckConstraint(
                                condition=models.Q(
                                    models.Q(
                                        ("chosen_id__gte", 1),
                                        ("chosen_id__lt", 2147483648),
                                        ("state", "chosen"),
                                    ),
                                    models.Q(
                                        ("chosen_id__isnull", True),
                                        ("chosen_name__isnull", True),
                                    ),
                                    _connector="OR",
                                ),
                                name="setup_lookup_choice_shape",
                            ),
                            models.CheckConstraint(
                                condition=models.Q(
                                    models.Q(
                                        ("scrubbed_at__isnull", False),
                                        ("state__in", ["chosen", "discarded"]),
                                    ),
                                    models.Q(
                                        ("scrubbed_at__isnull", True),
                                        (
                                            "state__in",
                                            ["queued", "running", "ready", "failed"],
                                        ),
                                    ),
                                    _connector="OR",
                                ),
                                name="setup_lookup_scrub_shape",
                            ),
                        ],
                    },
                ),
            ],
        )
    ]
