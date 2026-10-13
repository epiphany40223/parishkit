"""Forward migration: accept a large ParishSoft change once (ADM-13 PR 5a, #530).

Its SQL is the frozen file ``schema/migrations/0080_drop_acceptance.sql``
(prefix 0080 in the repository-wide file sequence), read whole and never
parsed. It creates ``stewardship_source_drop_acceptance`` with its unique
attempt, key, index and guard trigger (a row is admitted only from the web
login, for a freshly signed-in Administrator, for the newest refusal with no
full refresh promoted since and the counts as recorded, while no other
acceptance is live; it is never changed or deleted), and the invoker
``stewardship_source_drop_acceptance_active_v1()`` that says which
acceptance is live. It ends with a DO block that refuses to commit unless all
of it is installed as declared. The state operation records the model in
Django's model state, so ``makemigrations --check`` stays clean. Reversing
needs its own forward migration.

It follows the users swap (#952), so the worker's read grant on the table
waits for it (runtime_grants.CLOSURE_TABLES). The web's grants come with
System health's button (ADM-13 PR 5b).
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
    / "0080_drop_acceptance.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_source", "0007_corpus_evidence_from_snapshot"),
        # The migration that installed the previous frozen file on main
        # (0079), so the files apply in prefix order. It follows the users
        # swap, so `migrate --users-upgrade-base` never applies this file and
        # its table is registered in runtime_grants.CLOSURE_TABLES.
        ("stewardship_jobs", "0029_retry_refusal"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))
            ],
            state_operations=[
                migrations.CreateModel(
                    name="SourceDropAcceptance",
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
                        ("actor_id", models.UUIDField()),
                        ("session_id", models.UUIDField()),
                        (
                            "authenticated_at",
                            parishkit.stewardship.storage.UTCDateTimeField(),
                        ),
                        ("counts", models.JSONField()),
                        (
                            "expires_at",
                            parishkit.stewardship.storage.UTCDateTimeField(
                                db_default=django.db.models.functions.datetime.Now()
                            ),
                        ),
                        (
                            "attempt",
                            models.OneToOneField(
                                on_delete=django.db.models.deletion.PROTECT,
                                related_name="drop_acceptance",
                                to="stewardship_source.sourcerefreshattempt",
                            ),
                        ),
                    ],
                    options={
                        "db_table": "stewardship_source_drop_acceptance",
                        "indexes": [
                            models.Index(
                                models.OrderBy(models.F("created_at"), descending=True),
                                name="source_drop_acceptance_newest",
                            )
                        ],
                    },
                ),
            ],
        ),
    ]
