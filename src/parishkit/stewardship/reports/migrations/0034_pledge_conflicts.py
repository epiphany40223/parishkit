"""Forward migration: refresh checks of To review and conflicting pledges (#1747).

Its SQL is the frozen file ``schema/migrations/0135_pledge_conflicts.sql``
(prefix 0135 in the repository-wide file sequence, provisional until the
coordinator assigns it at merge), read whole and never parsed. It creates
``stewardship_pledge_open_check`` (one immutable row per refresh and To
review pledge, which only the promoting source worker writes), adds a
Conflict outcome to 0134's pledge refresh checks and the item's A
(``baseline_pledge``) to 0127's pledge reviews, and installs the shared
stage selection, the worker's definer selection and the open checks' guard;
it replaces 0134's check guard and pledge review guard in place. It ends with
a DO block that refuses to commit unless all of it is installed. The state
operations record the same changes in Django's model state, so
``makemigrations --check`` stays clean. Reversing needs its own forward
migration, so, like the baseline, it has no reverse operation.

It depends on the reports app's previous migration (0134's, whose table and
guards it changes), which installs the previous frozen file, so the files
apply in prefix order, and on the apps whose tables the new one references.
Its table is registered in ``runtime_grants.CLOSURE_TABLES``, which keeps it
in the users swap's closure.
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
    / "0135_pledge_conflicts.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_campaigns", "0014_digest_skip_dates"),
        ("stewardship_reports", "0033_pledge_refresh_checks"),
        ("stewardship_responses", "0014_census_pipeline_review"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))
            ],
            state_operations=[
                migrations.CreateModel(
                    name="PledgeOpenCheck",
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
                            "family",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.PROTECT,
                                related_name="pledge_open_checks",
                                to="stewardship_campaigns.familycampaign",
                            ),
                        ),
                        (
                            "submission",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.PROTECT,
                                related_name="pledge_open_checks",
                                to="stewardship_responses.submission",
                            ),
                        ),
                    ],
                    options={
                        "db_table": "stewardship_pledge_open_check",
                        "constraints": [
                            models.UniqueConstraint(
                                fields=("snapshot_id", "family"),
                                name="pledge_open_check_once",
                            ),
                            models.CheckConstraint(
                                condition=models.Q(
                                    (
                                        "outcome__in",
                                        ("confirmed", "unchanged", "conflict"),
                                    )
                                ),
                                name="pledge_open_check_outcome",
                            ),
                            models.CheckConstraint(
                                condition=models.Q(("actor_id__isnull", True)),
                                name="pledge_open_check_identity",
                            ),
                        ],
                    },
                ),
                migrations.RemoveConstraint(
                    model_name="pledgerefreshcheck",
                    name="pledge_refresh_check_outcome",
                ),
                migrations.AddConstraint(
                    model_name="pledgerefreshcheck",
                    constraint=models.CheckConstraint(
                        condition=models.Q(
                            (
                                "outcome__in",
                                (
                                    "confirmed",
                                    "unchanged",
                                    "entered_incorrectly",
                                    "ignored_now_matches",
                                    "conflict",
                                ),
                            )
                        ),
                        name="pledge_refresh_check_outcome",
                    ),
                ),
                migrations.AddField(
                    model_name="pledgereview",
                    name="baseline_pledge",
                    field=models.DecimalField(
                        decimal_places=2, max_digits=24, null=True
                    ),
                ),
            ],
        ),
    ]
