"""Forward migration: the roster Entered in ParishSoft tick (#528, step 4).

Its SQL is the frozen file ``schema/migrations/0033_roster_entries.sql``,
read whole and never parsed. Prefixes 0011 to 0025 and 0027 to 0032 are held
by other open pull requests; until they land, this migration depends on the
one that installs the latest frozen file in its base
(``0026_census_resolution.sql``, installed by
``stewardship_responses.0002_proposal_resolution``), and moves to the
migration that installs 0032 when it is rebased onto them. The file creates
``stewardship_ministry_roster_entry`` (each set or clear of the tick, with
who and when), its guard and its deferred audit check; nothing existing is
replaced. The state operation records the same table in Django's model
state, so ``makemigrations --check`` stays clean. Reversing needs its own
forward migration, so, like the baseline, it has no reverse operation.
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
    / "0033_roster_entries.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_workflows", "0001_initial"),
        # The migration that installed the previous frozen file in this base,
        # so the files apply in prefix order (see the module docstring).
        ("stewardship_responses", "0002_proposal_resolution"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))
            ],
            state_operations=[
                migrations.CreateModel(
                    name="MinistryRosterEntry",
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
                        ("sequence", models.PositiveBigIntegerField()),
                        ("entered", models.BooleanField()),
                        ("request_key", models.UUIDField()),
                        (
                            "request",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.PROTECT,
                                related_name="roster_entries",
                                to="stewardship_workflows.ministryrequest",
                            ),
                        ),
                    ],
                    options={
                        "db_table": "stewardship_ministry_roster_entry",
                        "constraints": [
                            models.UniqueConstraint(
                                fields=("request", "sequence"),
                                name="ministry_roster_sequence",
                            ),
                            models.UniqueConstraint(
                                fields=("actor_id", "request_key"),
                                name="ministry_roster_replay",
                            ),
                            models.CheckConstraint(
                                condition=models.Q(
                                    actor_id__isnull=False, sequence__gte=1
                                ),
                                name="ministry_roster_identity",
                            ),
                        ],
                    },
                ),
            ],
        ),
    ]
