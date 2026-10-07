"""Forward migration: the slot decision record of the refresh schedule (#632).

Its SQL is the frozen file ``schema/migrations/0014_slot_decisions.sql``
(the next prefix in the repository-wide file sequence after 0013, hence
the dependency on the migration that installs it), read whole and never
parsed. It creates ``stewardship_source_slot_decision`` with its guards,
the checks a refresh tick and a slot decision share
(``stewardship_refresh_slot_due_v1``), and replaces the refresh-tick guard
so that it calls them and refuses a tick for a slot recorded as skipped. It
ends with a DO block that refuses to commit unless all of it is installed.
The state operations record the ``SourceSlotDecision`` model in Django's
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
    / "0014_slot_decisions.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_accounts", "0004_automation_sessions"),
        ("stewardship_source", "0005_refresh_schedule"),
        # The migration that installed the previous frozen file (0013), so
        # the files apply in prefix order.
        ("stewardship_accounts", "0005_automation_fresh_guards"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))
            ],
            state_operations=[
                migrations.CreateModel(
                    name="SourceSlotDecision",
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
                        ("cause", models.CharField(max_length=16)),
                        ("due_at", parishkit.stewardship.storage.UTCDateTimeField()),
                        ("timezone", models.CharField(max_length=128)),
                        ("nightly_time", models.CharField(max_length=5)),
                        ("slot_key", models.CharField(max_length=64, unique=True)),
                        ("decision", models.CharField(max_length=8)),
                        ("window_cause", models.CharField(max_length=24, null=True)),
                        (
                            "configuration",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.PROTECT,
                                to="stewardship_accounts.appliedconfigurationversion",
                            ),
                        ),
                    ],
                    options={
                        "db_table": "stewardship_source_slot_decision",
                        "indexes": [
                            models.Index(
                                fields=["due_at"], name="source_slot_decision_due"
                            )
                        ],
                        "constraints": [
                            models.CheckConstraint(
                                condition=models.Q(
                                    ("slot_key__regex", "^[0-9a-f]{64}$")
                                ),
                                name="source_slot_decision_key",
                            ),
                            models.CheckConstraint(
                                condition=models.Q(
                                    (
                                        "nightly_time__regex",
                                        "^([01][0-9]|2[0-3]):[0-5][0-9]$",
                                    )
                                ),
                                name="source_slot_decision_time",
                            ),
                            models.CheckConstraint(
                                condition=models.Q(
                                    ("cause__in", ("nightly", "delta", "catch_up"))
                                ),
                                name="source_slot_decision_cause",
                            ),
                            models.CheckConstraint(
                                condition=models.Q(
                                    ("decision__in", ("skipped", "held"))
                                ),
                                name="source_slot_decision_kind",
                            ),
                            models.CheckConstraint(
                                condition=models.Q(
                                    models.Q(
                                        ("decision", "skipped"),
                                        (
                                            "window_cause__in",
                                            (
                                                "reminder_preparing",
                                                "reminder_sending",
                                                "initial_sending",
                                            ),
                                        ),
                                        models.Q(("cause", "catch_up"), _negated=True),
                                    ),
                                    models.Q(
                                        ("decision", "held"),
                                        ("window_cause__isnull", True),
                                    ),
                                    _connector="OR",
                                ),
                                name="source_slot_decision_window",
                            ),
                        ],
                    },
                ),
            ],
        ),
    ]
