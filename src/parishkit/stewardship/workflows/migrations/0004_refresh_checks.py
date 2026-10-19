"""Forward migration: the baseline refresh re-evaluation's refresh checks (#1689).

Its SQL is the frozen file ``schema/migrations/0132_refresh_checks.sql``
(number 0132, provisional until it is assigned in merge order), read whole
and never parsed. It creates ``stewardship_refresh_check`` (one immutable
row per refresh and final census change or decided Ministry request, which
only the promoting source worker writes, with the snapshot's own evidence)
and ``stewardship_refresh_check_start`` (when Not seen in ParishSoft starts
counting, written once here with an audit event), each with its guard. It
ends with a DO block that refuses to commit unless all of it is installed.
The state operations record the same models in Django's model state, so
``makemigrations --check`` stays clean. Reversing needs its own forward
migration, so, like the baseline, it has no reverse operation.
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
    / "0132_refresh_checks.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The migration that installs the previous frozen file (0131, the
        # Ministry review pipeline), so the files apply in prefix order, and
        # the census one (0128), whose changes the checks name. Both follow
        # the users swap, so its tables are in runtime_grants.CLOSURE_TABLES.
        ("stewardship_workflows", "0003_ministry_review_pipeline"),
        ("stewardship_responses", "0014_census_pipeline_review"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))
            ],
            state_operations=[
                migrations.CreateModel(
                    name="RefreshCheckStart",
                    fields=[
                        (
                            "singleton",
                            models.BooleanField(
                                default=True, primary_key=True, serialize=False
                            ),
                        ),
                        (
                            "started_at",
                            parishkit.stewardship.storage.UTCDateTimeField(),
                        ),
                        ("not_seen_after", models.PositiveSmallIntegerField(default=2)),
                    ],
                    options={
                        "db_table": "stewardship_refresh_check_start",
                        "constraints": [
                            models.CheckConstraint(
                                condition=models.Q(
                                    ("not_seen_after__gte", 1),
                                    ("not_seen_after__lte", 10),
                                    ("singleton", True),
                                ),
                                name="refresh_check_start_one",
                            )
                        ],
                    },
                ),
                migrations.CreateModel(
                    name="RefreshCheck",
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
                        ("snapshot_id", models.UUIDField()),
                        ("full_refresh", models.BooleanField()),
                        ("item_version", models.PositiveBigIntegerField()),
                        ("outcome", models.CharField(max_length=24)),
                        ("observed", models.JSONField(null=True)),
                        (
                            "proposal",
                            models.ForeignKey(
                                null=True,
                                on_delete=django.db.models.deletion.PROTECT,
                                related_name="+",
                                to="stewardship_responses.proposedchange",
                            ),
                        ),
                        (
                            "request",
                            models.ForeignKey(
                                null=True,
                                on_delete=django.db.models.deletion.PROTECT,
                                related_name="+",
                                to="stewardship_workflows.ministryrequest",
                            ),
                        ),
                    ],
                    options={
                        "db_table": "stewardship_refresh_check",
                        "constraints": [
                            models.UniqueConstraint(
                                fields=("snapshot_id", "proposal"),
                                name="refresh_check_proposal_once",
                            ),
                            models.UniqueConstraint(
                                fields=("snapshot_id", "request"),
                                name="refresh_check_request_once",
                            ),
                            models.CheckConstraint(
                                condition=models.Q(
                                    (
                                        "outcome__in",
                                        (
                                            "confirmed",
                                            "unchanged",
                                            "entered_incorrectly",
                                            "ignored_now_matches",
                                        ),
                                    )
                                ),
                                name="refresh_check_outcome",
                            ),
                            models.CheckConstraint(
                                condition=models.Q(
                                    models.Q(
                                        ("proposal__isnull", False),
                                        ("request__isnull", True),
                                    ),
                                    models.Q(
                                        ("proposal__isnull", True),
                                        ("request__isnull", False),
                                    ),
                                    _connector="OR",
                                ),
                                name="refresh_check_one_item",
                            ),
                            models.CheckConstraint(
                                condition=models.Q(
                                    ("actor_id__isnull", True),
                                    ("item_version__gte", 1),
                                ),
                                name="refresh_check_identity",
                            ),
                        ],
                    },
                ),
            ],
        ),
    ]
