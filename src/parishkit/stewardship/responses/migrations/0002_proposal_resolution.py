"""Forward migration: Staff and Admin resolve By-hand census changes (#528).

Its SQL is the frozen file ``schema/migrations/0060_census_resolution.sql``
(prefix 0060, assigned in the repository-wide file sequence), read whole and
never parsed. It creates ``stewardship_proposal_resolution`` (the history of
each tick, Ignore and reopen), its guard and its deferred effect check, and
replaces ``stewardship_response_derived_guard_v1`` with its current body plus
the branch that lets the web login apply exactly what a resolution row
records. The state operation records the same table in Django's model state,
so ``makemigrations --check`` stays clean. Reversing needs its own forward
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
    / "0060_census_resolution.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_responses", "0001_initial"),
        # The migration that installed the previous frozen file (0059,
        # #1018), so the files apply in prefix order. It follows the users
        # swap, so `migrate --users-upgrade-base` never applies this file
        # and its table is registered in runtime_grants.CLOSURE_TABLES.
        ("stewardship_reports", "0014_campaign_archives"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))
            ],
            state_operations=[
                migrations.CreateModel(
                    name="ProposalResolution",
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
                        ("expected_version", models.PositiveBigIntegerField()),
                        ("request_key", models.UUIDField()),
                        ("action", models.CharField(max_length=12)),
                        ("note", models.TextField(blank=True, default="")),
                        (
                            "proposal",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.PROTECT,
                                related_name="resolutions",
                                to="stewardship_responses.proposedchange",
                            ),
                        ),
                    ],
                    options={
                        "db_table": "stewardship_proposal_resolution",
                        "constraints": [
                            models.UniqueConstraint(
                                fields=("proposal", "expected_version"),
                                name="proposal_resolution_version",
                            ),
                            models.UniqueConstraint(
                                fields=("actor_id", "request_key"),
                                name="proposal_resolution_replay",
                            ),
                            models.CheckConstraint(
                                condition=models.Q(
                                    actor_id__isnull=False, expected_version__gte=1
                                ),
                                name="proposal_resolution_identity",
                            ),
                            models.CheckConstraint(
                                condition=models.Q(
                                    action__in=("entered", "ignored", "reopened")
                                ),
                                name="proposal_resolution_action",
                            ),
                        ],
                    },
                ),
            ],
        ),
    ]
