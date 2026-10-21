"""Forward migration: census Add on the review worklist (#1710).

Its SQL is the frozen file ``schema/migrations/0129_census_additions.sql``
(number 0129, provisional until it is assigned in merge order), read whole
and never parsed. It creates ``stewardship_census_addition`` (a census change
Staff or an Administrator added that no Family answer asked for) and
``stewardship_census_addition_resolution`` (its one later step: Entered by
hand or withdrawn), each with an INSERT-only guard and a deferred audit
check, and replaces nothing. It ends with a DO block that refuses to commit
unless all of it is installed. The state operations record the two models in
Django's model state, so ``makemigrations --check`` stays clean. Reversing
needs its own forward migration.

It follows the users swap (#952), so its tables are granted only once it is
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
    / "0129_census_additions.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # This app's previous migration, which installs the previous frozen
        # file (0128), so the files apply in prefix order and the app keeps
        # one leaf.
        ("stewardship_responses", "0014_census_pipeline_review"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))
            ],
            state_operations=[
                migrations.CreateModel(
                    name="CensusAddition",
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
                        ("entity_kind", models.CharField(max_length=20)),
                        ("entity_key", models.CharField(max_length=36)),
                        ("field", models.CharField(max_length=40)),
                        ("value", models.JSONField()),
                        ("request_key", models.UUIDField()),
                        ("note", models.TextField(blank=True, default="")),
                        ("baseline", models.JSONField(editable=False, null=True)),
                        (
                            "baseline_source_id",
                            models.UUIDField(editable=False, null=True),
                        ),
                        (
                            "family",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.PROTECT,
                                to="stewardship_campaigns.familycampaign",
                            ),
                        ),
                        (
                            "in_place_of",
                            models.ForeignKey(
                                null=True,
                                on_delete=django.db.models.deletion.PROTECT,
                                related_name="additions_in_place",
                                to="stewardship_responses.proposedchange",
                            ),
                        ),
                    ],
                    options={
                        "db_table": "stewardship_census_addition",
                    },
                ),
                migrations.CreateModel(
                    name="CensusAdditionResolution",
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
                        ("action", models.CharField(max_length=12)),
                        ("request_key", models.UUIDField()),
                        ("note", models.TextField(blank=True, default="")),
                        (
                            "addition",
                            models.OneToOneField(
                                on_delete=django.db.models.deletion.PROTECT,
                                related_name="resolution",
                                to="stewardship_responses.censusaddition",
                            ),
                        ),
                    ],
                    options={
                        "db_table": "stewardship_census_addition_resolution",
                    },
                ),
                migrations.AddConstraint(
                    model_name="censusaddition",
                    constraint=models.UniqueConstraint(
                        fields=("actor_id", "request_key"),
                        name="census_addition_replay",
                    ),
                ),
                migrations.AddConstraint(
                    model_name="censusaddition",
                    constraint=models.CheckConstraint(
                        condition=models.Q(
                            ("actor_id__isnull", False),
                            ("entity_kind__in", ("family", "member")),
                            models.Q(("entity_key", ""), _negated=True),
                            models.Q(("field", ""), _negated=True),
                        ),
                        name="census_addition_identity",
                    ),
                ),
                migrations.AddConstraint(
                    model_name="censusadditionresolution",
                    constraint=models.UniqueConstraint(
                        fields=("actor_id", "request_key"),
                        name="census_addition_resolution_replay",
                    ),
                ),
                migrations.AddConstraint(
                    model_name="censusadditionresolution",
                    constraint=models.CheckConstraint(
                        condition=models.Q(
                            ("actor_id__isnull", False),
                            ("action__in", ("entered", "withdrawn")),
                        ),
                        name="census_addition_resolution_identity",
                    ),
                ),
            ],
        )
    ]
