"""Forward migration: example Families of a refused large change (ADM-13 PR 5c, #530).

Its SQL is the frozen file ``schema/migrations/0085_drop_examples.sql``
(prefix 0085 in the repository-wide file sequence), read whole and never
parsed. It creates ``stewardship_source_drop_example`` with its keys, checks,
index and guard trigger (a row is admitted only from the worker, for a
count that failed whose drop count was recorded no earlier than the
inserting transaction began, never for an empty load;
nobody changes one, and only the worker and the schema owner delete them),
and the invoker ``stewardship_source_drop_example_attempt_v1()`` that names
the one refusal whose examples may be shown. It ends with a DO block that
refuses to commit unless all of it is installed as declared. The state
operation records the model in Django's model state, so ``makemigrations
--check`` stays clean. Reversing needs its own forward migration.

It follows the users swap (#952), so its grants (the worker's SELECT, INSERT
and DELETE, the web's SELECT) wait for it (runtime_grants.CLOSURE_TABLES).
"""

import uuid
from pathlib import Path

import django.db.models.deletion
import django.db.models.functions.datetime
from django.db import migrations, models

import parishkit.stewardship.storage

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0085_drop_examples.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The migration that installed the previous frozen file (0084), so
        # the files apply in prefix order. It follows the users swap, so
        # `migrate --users-upgrade-base` never applies this file and its
        # table is registered in runtime_grants.CLOSURE_TABLES.
        ("stewardship_reports", "0023_campaign_archive_exports"),
        ("stewardship_source", "0008_drop_acceptance"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))
            ],
            state_operations=[
                migrations.CreateModel(
                    name="SourceDropExample",
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
                                db_default=django.db.models.functions.datetime.Now()
                            ),
                        ),
                        ("measure", models.CharField(max_length=32)),
                        ("position", models.PositiveSmallIntegerField()),
                        ("family_duid", models.PositiveBigIntegerField()),
                        ("family_name", models.CharField(max_length=300)),
                        (
                            "attempt",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.PROTECT,
                                related_name="drop_examples",
                                to="stewardship_source.sourcerefreshattempt",
                            ),
                        ),
                    ],
                    options={
                        "db_table": "stewardship_source_drop_example",
                        "constraints": [
                            models.UniqueConstraint(
                                fields=("attempt", "measure", "position"),
                                name="source_drop_example_position",
                            ),
                            models.UniqueConstraint(
                                fields=("attempt", "measure", "family_duid"),
                                name="source_drop_example_family",
                            ),
                            models.CheckConstraint(
                                condition=models.Q(
                                    (
                                        "measure__in",
                                        (
                                            "family",
                                            "member",
                                            "portal_eligible_families",
                                            "email_eligible_families",
                                            "active_head_families",
                                            "valid_email_contacts",
                                        ),
                                    )
                                ),
                                name="source_drop_example_measure",
                            ),
                            models.CheckConstraint(
                                condition=models.Q(
                                    ("position__gte", 1), ("position__lte", 5)
                                ),
                                name="source_drop_example_place",
                            ),
                            models.CheckConstraint(
                                condition=models.Q(
                                    ("family_duid__gt", 0),
                                    models.Q(("family_name", ""), _negated=True),
                                ),
                                name="source_drop_example_family_shape",
                            ),
                        ],
                    },
                ),
            ],
        ),
    ]
