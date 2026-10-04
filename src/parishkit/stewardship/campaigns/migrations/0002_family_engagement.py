"""Durable Family engagement for response reporting (#477).

The first forward migration on a live Production database. Its SQL is the
frozen file ``schema/migrations/0002_family_engagement.sql``, read whole and
never parsed: the engagement table and its guards, the widened Testing
cleanup category and operational event lists, and ``CREATE OR REPLACE`` of
the two cleanup functions that enumerate categories, copied verbatim from
``cleanup.sql`` and ``production.sql`` as they stand at this release. A
digest test pins the frozen file, and a second test checks that the latest
migration's copy of each replaced function still equals the fresh-install
file's, so the two cannot drift apart unnoticed; a later change to either
gets its own numbered migration. The frozen text starts with ``SET LOCAL
check_function_bodies = false``, so a function body that names an object a
later migration owns cannot break this one.

A fresh install runs the baseline (0001, whose files already carry the final
function bodies and category lists) and then this migration, which creates
the table and re-creates those functions unchanged; an upgraded database ends
in the same catalog, verified by hand against a main-branch database before
release (the evidence is in the pull request). Like the baseline, it has no
reverse operation.
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
    / "0002_family_engagement.sql"
)

CLEANUP_CATEGORIES = (
    "baselines",
    "session_data",
    "family_sessions",
    "ministry_requests",
    "occurrences",
    "occurrence_events",
    "outbox_events",
    "outbox_messages",
    "outbox_renders",
    "proposals",
    "rehearsal_credentials",
    "rehearsal_macs",
    "schedule_fulfillments",
    "source_pins",
    "submission_receipts",
    "submissions",
    "prior_inventory_targets",
    "daily_digest_recipients",
    "daily_digest_ready",
    "daily_digest_snapshots",
    "daily_digest_fact_pins",
    "recovery_replacements",
    "weekly_digest_recipients",
    "weekly_digest_snapshots",
    "engagement",
)


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_campaigns", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8")),
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.CreateModel(
                    name="FamilyEngagement",
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
                        (
                            "updated_at",
                            parishkit.stewardship.storage.UTCDateTimeField(
                                db_default=django.db.models.functions.datetime.Now(),
                                editable=False,
                            ),
                        ),
                        (
                            "version",
                            models.PositiveBigIntegerField(default=1, editable=False),
                        ),
                        ("mode", models.CharField(max_length=4)),
                        ("rehearsal_epoch_id", models.UUIDField(null=True)),
                        (
                            "first_link_at",
                            parishkit.stewardship.storage.UTCDateTimeField(null=True),
                        ),
                        (
                            "first_form_at",
                            parishkit.stewardship.storage.UTCDateTimeField(null=True),
                        ),
                        (
                            "first_progress_at",
                            parishkit.stewardship.storage.UTCDateTimeField(null=True),
                        ),
                        (
                            "furthest_section",
                            models.CharField(blank=True, default="", max_length=24),
                        ),
                        (
                            "furthest_at",
                            parishkit.stewardship.storage.UTCDateTimeField(null=True),
                        ),
                        (
                            "last_seen_at",
                            parishkit.stewardship.storage.UTCDateTimeField(),
                        ),
                        (
                            "family",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.PROTECT,
                                to="stewardship_campaigns.familycampaign",
                            ),
                        ),
                    ],
                    options={
                        "db_table": "stewardship_family_engagement",
                        "abstract": False,
                    },
                ),
                migrations.RemoveConstraint(
                    model_name="productioncleanuptarget",
                    name="production_target_category",
                ),
                migrations.AddConstraint(
                    model_name="productioncleanuptarget",
                    constraint=models.CheckConstraint(
                        condition=models.Q(("category__in", list(CLEANUP_CATEGORIES))),
                        name="production_target_category",
                    ),
                ),
                migrations.AddIndex(
                    model_name="familyengagement",
                    index=models.Index(
                        fields=["mode", "first_link_at"], name="family_engagement_link"
                    ),
                ),
                migrations.AddConstraint(
                    model_name="familyengagement",
                    constraint=models.CheckConstraint(
                        condition=models.Q(("version__gte", 1)),
                        name="stewardship_campaigns_familyengagement_positive_version",
                    ),
                ),
                migrations.AddConstraint(
                    model_name="familyengagement",
                    constraint=models.UniqueConstraint(
                        fields=("family", "mode", "rehearsal_epoch_id"),
                        name="family_engagement_identity",
                        nulls_distinct=False,
                    ),
                ),
                migrations.AddConstraint(
                    model_name="familyengagement",
                    constraint=models.CheckConstraint(
                        condition=models.Q(
                            models.Q(
                                ("mode", "live"), ("rehearsal_epoch_id__isnull", True)
                            ),
                            models.Q(
                                ("mode", "test"), ("rehearsal_epoch_id__isnull", False)
                            ),
                            _connector="OR",
                        ),
                        name="family_engagement_mode_epoch",
                    ),
                ),
                migrations.AddConstraint(
                    model_name="familyengagement",
                    constraint=models.CheckConstraint(
                        condition=models.Q(
                            models.Q(
                                ("furthest_at__isnull", True), ("furthest_section", "")
                            ),
                            models.Q(
                                ("furthest_at__isnull", False),
                                (
                                    "furthest_section__in",
                                    (
                                        "welcome",
                                        "census",
                                        "members",
                                        "ministry",
                                        "financial",
                                        "closing",
                                        "additional",
                                        "review",
                                    ),
                                ),
                            ),
                            _connector="OR",
                        ),
                        name="family_engagement_furthest_shape",
                    ),
                ),
                migrations.AddConstraint(
                    model_name="familyengagement",
                    constraint=models.CheckConstraint(
                        condition=models.Q(
                            models.Q(
                                ("first_progress_at__isnull", True),
                                ("furthest_section__in", ["", "welcome"]),
                            ),
                            models.Q(
                                ("first_progress_at__isnull", False),
                                models.Q(
                                    ("furthest_section__in", ["", "welcome"]),
                                    _negated=True,
                                ),
                            ),
                            _connector="OR",
                        ),
                        name="family_engagement_progress_shape",
                    ),
                ),
                migrations.AddConstraint(
                    model_name="familyengagement",
                    constraint=models.CheckConstraint(
                        condition=models.Q(
                            ("first_link_at__isnull", False),
                            ("first_form_at__isnull", False),
                            ("furthest_at__isnull", False),
                            _connector="OR",
                        ),
                        name="family_engagement_evidence",
                    ),
                ),
                migrations.AddConstraint(
                    model_name="familyengagement",
                    constraint=models.CheckConstraint(
                        condition=models.Q(
                            models.Q(
                                ("first_link_at__isnull", True),
                                ("first_link_at__lte", models.F("last_seen_at")),
                                _connector="OR",
                            ),
                            models.Q(
                                ("first_form_at__isnull", True),
                                ("first_form_at__lte", models.F("last_seen_at")),
                                _connector="OR",
                            ),
                            models.Q(
                                ("first_progress_at__isnull", True),
                                ("first_progress_at__lte", models.F("last_seen_at")),
                                _connector="OR",
                            ),
                            models.Q(
                                ("furthest_at__isnull", True),
                                ("furthest_at__lte", models.F("last_seen_at")),
                                _connector="OR",
                            ),
                        ),
                        name="family_engagement_seen_last",
                    ),
                ),
            ]
        ),
    ]
