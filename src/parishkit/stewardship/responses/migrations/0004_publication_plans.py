"""Forward migration: publication plans and their outcomes (#528, DAT-09.01).

Its SQL is the frozen file ``schema/migrations/0087_publication_plans.sql``
(prefix 0087, assigned in the repository-wide file sequence), read whole and
never parsed. It creates the five append-only tables of a ParishSoft
publication (plan, entity, field, confirmation and outcome), their guards
and deferred audit checks, and the worker's audit answer
``stewardship_publication_audited_v1``; nothing existing is replaced. The
state operations record the same tables in Django's model state, so
``makemigrations --check`` stays clean. Reversing needs its own forward
migration, so, like the baseline, it has no reverse operation.

It follows the users swap (#952), so its tables are registered in
``runtime_grants.CLOSURE_TABLES`` and its definer in ``CLOSURE_FUNCTIONS``.
The dependency on the previous frozen file's migration must be re-chained
when it is merged after 0082 to 0090.
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
    / "0087_publication_plans.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_responses", "0003_staff_entered"),
        ("stewardship_campaigns", "0001_initial"),
        # The migration that installs the previous frozen file (0086,
        # snapshot statistics), so the files apply in prefix order. It
        # follows the users swap, so `migrate --users-upgrade-base` never
        # applies this file.
        ("stewardship_source", "0010_snapshot_statistics"),
        ("stewardship_jobs", "0030_bounce_report"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))
            ],
            state_operations=[
                migrations.CreateModel(
                    name="PublicationPlan",
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
                        ("organization_id", models.PositiveBigIntegerField()),
                        ("testing", models.BooleanField()),
                        ("request_key", models.UUIDField()),
                        (
                            "campaign",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.PROTECT,
                                related_name="+",
                                to="stewardship_campaigns.campaign",
                            ),
                        ),
                        (
                            "source_snapshot",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.PROTECT,
                                related_name="+",
                                to="stewardship_source.sourcesnapshot",
                            ),
                        ),
                    ],
                    options={
                        "db_table": "stewardship_publication_plan",
                    },
                ),
                migrations.CreateModel(
                    name="PublicationEntity",
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
                        ("blocked", models.BooleanField()),
                        ("read_sha256", models.CharField(max_length=64, null=True)),
                        ("planned_sha256", models.CharField(max_length=64, null=True)),
                        (
                            "plan",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.PROTECT,
                                related_name="entities",
                                to="stewardship_responses.publicationplan",
                            ),
                        ),
                    ],
                    options={
                        "db_table": "stewardship_publication_entity",
                    },
                ),
                migrations.CreateModel(
                    name="PublicationConfirmation",
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
                        ("session_id", models.UUIDField()),
                        (
                            "authenticated_at",
                            parishkit.stewardship.storage.UTCDateTimeField(),
                        ),
                        ("request_key", models.UUIDField()),
                        ("write_entities", models.PositiveIntegerField()),
                        ("write_fields", models.PositiveIntegerField()),
                        ("blocked_entities", models.PositiveIntegerField()),
                        (
                            "plan",
                            models.OneToOneField(
                                on_delete=django.db.models.deletion.PROTECT,
                                related_name="confirmation",
                                to="stewardship_responses.publicationplan",
                            ),
                        ),
                    ],
                    options={
                        "db_table": "stewardship_publication_confirmation",
                    },
                ),
                migrations.CreateModel(
                    name="PublicationField",
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
                        ("proposal_version", models.PositiveBigIntegerField()),
                        ("field", models.CharField(max_length=40)),
                        ("outcome", models.CharField(max_length=24)),
                        (
                            "entity",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.PROTECT,
                                related_name="planned_fields",
                                to="stewardship_responses.publicationentity",
                            ),
                        ),
                        (
                            "proposal",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.PROTECT,
                                related_name="+",
                                to="stewardship_responses.proposedchange",
                            ),
                        ),
                    ],
                    options={
                        "db_table": "stewardship_publication_field",
                        "constraints": [
                            models.UniqueConstraint(
                                fields=("entity", "proposal"),
                                name="publication_field_identity",
                            ),
                            models.CheckConstraint(
                                condition=models.Q(
                                    (
                                        "outcome__in",
                                        (
                                            "write",
                                            "already_in_parishsoft",
                                            "conflict",
                                            "cannot_check",
                                            "not_approved",
                                            "by_hand",
                                        ),
                                    ),
                                    ("proposal_version__gte", 1),
                                ),
                                name="publication_field_shape",
                            ),
                        ],
                    },
                ),
                migrations.CreateModel(
                    name="PublicationOutcome",
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
                        ("attempt", models.PositiveIntegerField()),
                        ("result", models.CharField(max_length=12)),
                        (
                            "failure_kind",
                            models.CharField(blank=True, default="", max_length=40),
                        ),
                        ("fence", models.PositiveBigIntegerField()),
                        (
                            "entity",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.PROTECT,
                                related_name="outcomes",
                                to="stewardship_responses.publicationentity",
                            ),
                        ),
                    ],
                    options={
                        "db_table": "stewardship_publication_outcome",
                        "constraints": [
                            models.UniqueConstraint(
                                fields=("entity", "attempt"),
                                name="publication_outcome_attempt",
                            ),
                            models.CheckConstraint(
                                condition=models.Q(
                                    ("attempt__gte", 1),
                                    ("fence__gte", 1),
                                    (
                                        "result__in",
                                        ("published", "invalidated", "failed"),
                                    ),
                                ),
                                name="publication_outcome_shape",
                            ),
                            models.CheckConstraint(
                                condition=models.Q(
                                    models.Q(
                                        (
                                            "failure_kind__regex",
                                            "^[a-z][a-z0-9_]{0,39}$",
                                        ),
                                        ("result", "failed"),
                                    ),
                                    models.Q(
                                        models.Q(("result", "failed"), _negated=True),
                                        ("failure_kind", ""),
                                    ),
                                    _connector="OR",
                                ),
                                name="publication_outcome_failure",
                            ),
                        ],
                    },
                ),
                migrations.AddConstraint(
                    model_name="publicationplan",
                    constraint=models.UniqueConstraint(
                        fields=("actor_id", "request_key"),
                        name="publication_plan_replay",
                    ),
                ),
                migrations.AddConstraint(
                    model_name="publicationplan",
                    constraint=models.CheckConstraint(
                        condition=models.Q(("actor_id__isnull", False)),
                        name="publication_plan_actor",
                    ),
                ),
                migrations.AddConstraint(
                    model_name="publicationentity",
                    constraint=models.UniqueConstraint(
                        fields=("plan", "entity_kind", "entity_key"),
                        name="publication_entity_identity",
                    ),
                ),
                migrations.AddConstraint(
                    model_name="publicationentity",
                    constraint=models.CheckConstraint(
                        condition=models.Q(
                            ("entity_kind__in", ("member", "family")),
                            models.Q(("entity_key", ""), _negated=True),
                        ),
                        name="publication_entity_shape",
                    ),
                ),
                migrations.AddConstraint(
                    model_name="publicationentity",
                    constraint=models.CheckConstraint(
                        condition=models.Q(
                            models.Q(
                                ("planned_sha256__isnull", True),
                                ("read_sha256__isnull", True),
                            ),
                            models.Q(
                                ("blocked", False),
                                ("planned_sha256__regex", "^[0-9a-f]{64}$"),
                                ("read_sha256__regex", "^[0-9a-f]{64}$"),
                            ),
                            _connector="OR",
                        ),
                        name="publication_entity_hashes",
                    ),
                ),
                migrations.AddConstraint(
                    model_name="publicationconfirmation",
                    constraint=models.UniqueConstraint(
                        fields=("actor_id", "request_key"),
                        name="publication_confirmation_replay",
                    ),
                ),
                migrations.AddConstraint(
                    model_name="publicationconfirmation",
                    constraint=models.CheckConstraint(
                        condition=models.Q(
                            ("actor_id__isnull", False), ("write_entities__gte", 1)
                        ),
                        name="publication_confirmation_shape",
                    ),
                ),
            ],
        )
    ]
