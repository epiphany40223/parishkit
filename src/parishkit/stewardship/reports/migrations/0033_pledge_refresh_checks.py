"""Forward migration: the refresh re-evaluation of hand-entered pledges (#1704).

Its SQL is the frozen file ``schema/migrations/0134_pledge_refresh_checks.sql``
(prefix 0134 in the repository-wide file sequence, provisional until the
coordinator assigns it at merge), read whole and never parsed. It creates
``stewardship_pledge_refresh_check`` (one immutable row per refresh and
decided pledge, which only the promoting source worker writes, with the
snapshot's own amount), the upcoming-year pledge selection its guard and the
pledge review guard share, and the guard; and it replaces 0127's pledge
review guard in place so an Entered by hand mark may record ParishSoft's
amount. It ends with a DO block that refuses to commit unless all of it is
installed. The state operation records the table in Django's model state,
so ``makemigrations --check`` stays clean. Reversing needs its own forward
migration, so, like the baseline, it has no reverse operation.

It depends on the reports app's previous migration (0127's, whose rows the
checks name and whose guard it replaces), on the refresh checks' migration
(0132, in the workflows app, whose Not seen start the checks share) and on
the migration that installs the previous frozen file (0133, in the responses
app), so the files apply in prefix order. That keeps it in the
users swap's closure: its table is registered in
``runtime_grants.CLOSURE_TABLES``.
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
    / "0134_pledge_refresh_checks.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_reports", "0032_pledge_review"),
        ("stewardship_workflows", "0004_refresh_checks"),
        ("stewardship_responses", "0017_entered_incorrectly_fixes"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))
            ],
            state_operations=[
                migrations.CreateModel(
                    name="PledgeRefreshCheck",
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
                        (
                            "observed",
                            models.DecimalField(decimal_places=2, max_digits=24),
                        ),
                        (
                            "review",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.PROTECT,
                                related_name="refresh_checks",
                                to="stewardship_reports.pledgereview",
                            ),
                        ),
                    ],
                    options={
                        "db_table": "stewardship_pledge_refresh_check",
                        "constraints": [
                            models.UniqueConstraint(
                                fields=("snapshot_id", "review"),
                                name="pledge_refresh_check_once",
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
                                name="pledge_refresh_check_outcome",
                            ),
                            models.CheckConstraint(
                                condition=models.Q(
                                    ("actor_id__isnull", True),
                                    ("item_version__gte", 1),
                                ),
                                name="pledge_refresh_check_identity",
                            ),
                        ],
                    },
                ),
            ],
        ),
    ]
