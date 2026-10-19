"""Forward migration: Additional information and Limitations reviews (#1685).

Its SQL is the frozen file
``schema/migrations/0126_information_limitation_review.sql`` (number 0126,
provisional until it is assigned in merge order), read whole and never
parsed. It adds each Additional information Staff revision's review
decision and replaces the revision guard so every new revision states one
(and reopening an ignored request needs a note); it creates
``stewardship_limitation_review`` (one immutable row per decision on a
cannot-participate or cannot-attend answer), its guard and deferred audit
check, and ``stewardship_limitations_report_v2``, the Limitations report
with each answer's key and decision. It ends with a DO block that refuses to
commit unless all of it is installed. The state operations record the same
fields, table and constraints in Django's model state, so ``makemigrations
--check`` stays clean. Reversing needs its own forward migration, so, like
the baseline, it has no reverse operation.
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
    / "0126_information_limitation_review.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # This app's previous migration, so the app keeps one leaf, and the
        # migration that installs the previous frozen file (0125), so the
        # files apply in prefix order.
        ("stewardship_responses", "0012_gender_by_hand"),
        ("stewardship_accounts", "0030_secret_request_session"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))
            ],
            state_operations=[
                migrations.CreateModel(
                    name="LimitationReview",
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
                        ("kind", models.CharField(max_length=16)),
                        (
                            "member_key",
                            models.CharField(blank=True, default="", max_length=64),
                        ),
                        ("expected_version", models.PositiveBigIntegerField()),
                        ("request_key", models.UUIDField()),
                        ("decision", models.CharField(max_length=12)),
                        ("note", models.TextField(blank=True, default="")),
                    ],
                    options={
                        "db_table": "stewardship_limitation_review",
                    },
                ),
                migrations.AddField(
                    model_name="additionalinformationrevision",
                    name="review_decision",
                    field=models.CharField(max_length=12, null=True),
                ),
                migrations.AddConstraint(
                    model_name="additionalinformationrevision",
                    constraint=models.CheckConstraint(
                        condition=models.Q(
                            ("review_decision__isnull", True),
                            models.Q(
                                ("follow_up_needed", True),
                                ("review_decision", "accepted"),
                            ),
                            models.Q(
                                ("followed_up", True), ("review_decision", "accepted")
                            ),
                            models.Q(
                                ("follow_up_needed", False),
                                ("followed_up", False),
                                ("review_decision__in", ("ignored", "none")),
                            ),
                            _connector="OR",
                        ),
                        name="information_revision_decision",
                    ),
                ),
                migrations.AddField(
                    model_name="limitationreview",
                    name="family",
                    field=models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to="stewardship_campaigns.familycampaign",
                    ),
                ),
                migrations.AddField(
                    model_name="limitationreview",
                    name="submission",
                    field=models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to="stewardship_responses.submission",
                    ),
                ),
                migrations.AddConstraint(
                    model_name="limitationreview",
                    constraint=models.UniqueConstraint(
                        fields=("family", "kind", "member_key", "expected_version"),
                        name="limitation_review_version",
                    ),
                ),
                migrations.AddConstraint(
                    model_name="limitationreview",
                    constraint=models.UniqueConstraint(
                        fields=("actor_id", "request_key"),
                        name="limitation_review_replay",
                    ),
                ),
                migrations.AddConstraint(
                    model_name="limitationreview",
                    constraint=models.CheckConstraint(
                        condition=models.Q(("actor_id__isnull", False)),
                        name="limitation_review_identity",
                    ),
                ),
                migrations.AddConstraint(
                    model_name="limitationreview",
                    constraint=models.CheckConstraint(
                        condition=models.Q(
                            ("kind__in", ("member", "proposed_member", "family"))
                        ),
                        name="limitation_review_kind",
                    ),
                ),
                migrations.AddConstraint(
                    model_name="limitationreview",
                    constraint=models.CheckConstraint(
                        condition=models.Q(
                            ("decision__in", ("accepted", "ignored", "none", "handled"))
                        ),
                        name="limitation_review_decision",
                    ),
                ),
                migrations.AddConstraint(
                    model_name="limitationreview",
                    constraint=models.CheckConstraint(
                        condition=models.Q(
                            models.Q(("kind", "family"), ("member_key", "")),
                            models.Q(
                                models.Q(("kind", "family"), _negated=True),
                                models.Q(("member_key", ""), _negated=True),
                            ),
                            _connector="OR",
                        ),
                        name="limitation_review_key",
                    ),
                ),
            ],
        ),
    ]
