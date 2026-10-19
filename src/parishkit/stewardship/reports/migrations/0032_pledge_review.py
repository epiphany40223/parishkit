"""Forward migration: the financial stream's review history (#1686).

Its SQL is the frozen file ``schema/migrations/0135_pledge_review.sql``
(prefix 0135 in the repository-wide file sequence, provisional until the
coordinator assigns it at merge), read whole and never parsed. It creates
``stewardship_pledge_review`` (one immutable row per Accept, Edit, Ignore,
Back to review and Entered by hand on a Family's pledge), the pledge answer
fingerprint and item selection the page and the guard share, the guard and
its deferred audit check. The state operation records the same table in
Django's model state, so ``makemigrations --check`` stays clean. Reversing
needs its own forward migration, so, like the baseline, it has no reverse
operation.

It depends on the reports app's previous migration and on the migration that
installs the previous frozen file (0122, in the audit app), so the files
apply in prefix order. That keeps it in the users swap's closure: its table
is registered in ``runtime_grants.CLOSURE_TABLES``.
"""

import uuid
from decimal import Decimal
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
    / "0135_pledge_review.sql"
)
ACTIONS = ("accept", "edit", "ignore", "back_to_review", "entered_by_hand")
DECISIONS = ("accepted", "accepted_edited", "ignored")
FREQUENCIES = ("weekly", "monthly", "quarterly", "annual")


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_reports", "0031_leader_request_contacts"),
        ("stewardship_campaigns", "0014_digest_skip_dates"),
        ("stewardship_responses", "0012_gender_by_hand"),
        # The migration that installs the previous frozen file (0122, the
        # automatic report views), so the files apply in prefix order.
        ("stewardship_audit", "0006_automatic_report_views"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))
            ],
            state_operations=[
                migrations.CreateModel(
                    name="PledgeReview",
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
                        ("request_key", models.UUIDField()),
                        ("action", models.CharField(max_length=16)),
                        ("decision", models.CharField(max_length=16, null=True)),
                        (
                            "reviewed_annual_pledge",
                            models.DecimalField(
                                decimal_places=2, max_digits=14, null=True
                            ),
                        ),
                        (
                            "reviewed_frequency",
                            models.CharField(max_length=12, null=True),
                        ),
                        ("entered", models.BooleanField()),
                        ("answer", models.JSONField()),
                        (
                            "marked_pledge",
                            models.DecimalField(
                                decimal_places=2, max_digits=14, null=True
                            ),
                        ),
                        ("marked_pledge_known", models.BooleanField()),
                        ("note", models.TextField(blank=True, default="")),
                        (
                            "family",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.PROTECT,
                                related_name="pledge_reviews",
                                to="stewardship_campaigns.familycampaign",
                            ),
                        ),
                        (
                            "submission",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.PROTECT,
                                related_name="pledge_reviews",
                                to="stewardship_responses.submission",
                            ),
                        ),
                    ],
                    options={
                        "db_table": "stewardship_pledge_review",
                        "constraints": [
                            models.UniqueConstraint(
                                fields=("family", "sequence"),
                                name="pledge_review_sequence",
                            ),
                            models.UniqueConstraint(
                                fields=("actor_id", "request_key"),
                                name="pledge_review_replay",
                            ),
                            models.CheckConstraint(
                                condition=models.Q(
                                    actor_id__isnull=False, sequence__gte=1
                                ),
                                name="pledge_review_identity",
                            ),
                            models.CheckConstraint(
                                condition=models.Q(action__in=ACTIONS),
                                name="pledge_review_action",
                            ),
                            models.CheckConstraint(
                                condition=models.Q(decision__isnull=True)
                                | models.Q(decision__in=DECISIONS),
                                name="pledge_review_decision",
                            ),
                            models.CheckConstraint(
                                condition=(
                                    models.Q(
                                        decision="accepted_edited",
                                        reviewed_annual_pledge__isnull=False,
                                        reviewed_annual_pledge__gte=0,
                                        reviewed_annual_pledge__lte=Decimal(
                                            "999999999.99"
                                        ),
                                    )
                                    & (
                                        models.Q(
                                            reviewed_frequency__isnull=False,
                                            reviewed_frequency__in=FREQUENCIES,
                                        )
                                        | models.Q(
                                            reviewed_frequency__isnull=True,
                                            reviewed_annual_pledge=0,
                                        )
                                    )
                                )
                                | models.Q(
                                    decision__isnull=True,
                                    reviewed_annual_pledge__isnull=True,
                                    reviewed_frequency__isnull=True,
                                )
                                | models.Q(
                                    decision__in=("accepted", "ignored"),
                                    reviewed_annual_pledge__isnull=True,
                                    reviewed_frequency__isnull=True,
                                ),
                                name="pledge_review_value_shape",
                            ),
                            models.CheckConstraint(
                                condition=models.Q(
                                    action="accept",
                                    decision="accepted",
                                    decision__isnull=False,
                                    entered=False,
                                )
                                | models.Q(
                                    action="edit",
                                    decision="accepted_edited",
                                    decision__isnull=False,
                                    entered=False,
                                )
                                | models.Q(
                                    action="ignore",
                                    decision="ignored",
                                    decision__isnull=False,
                                    entered=False,
                                )
                                | models.Q(
                                    action="back_to_review",
                                    decision__isnull=True,
                                    entered=False,
                                )
                                | models.Q(
                                    action="entered_by_hand",
                                    decision__isnull=False,
                                    decision__in=("accepted", "accepted_edited"),
                                    entered=True,
                                ),
                                name="pledge_review_action_state",
                            ),
                            models.CheckConstraint(
                                condition=models.Q(
                                    marked_pledge_known=True, entered=True
                                )
                                | models.Q(
                                    marked_pledge_known=False,
                                    marked_pledge__isnull=True,
                                ),
                                name="pledge_review_mark_shape",
                            ),
                        ],
                    },
                ),
            ],
        ),
    ]
