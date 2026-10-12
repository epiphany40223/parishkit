"""Forward migration: imported past-campaign archives (#1018, slice 2).

Its SQL is the frozen file ``schema/migrations/0059_campaign_archives.sql``
(prefix 0059, assigned in the repository-wide file sequence), read whole and
never parsed. It creates the four archive tables (``reports.archive_models``)
with their guards and one-active-row indexes, and replaces
``stewardship_safe_context_v1`` so the context allowlist admits the
import's audit keys and the archive check's timeout words. The state
operations record the same tables in Django's model state, so
``makemigrations --check`` stays clean. Reversing needs its own forward
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
    / "0059_campaign_archives.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The reports app's previous migration (it installed 0056, #970).
        ("stewardship_reports", "0013_retire_talents"),
        # The migration that installed the previous frozen file (0058,
        # #1024), so the files apply in prefix order. It follows the users
        # swap, so `migrate --users-upgrade-base` never applies this file
        # and its tables are registered in runtime_grants.CLOSURE_TABLES.
        ("stewardship_jobs", "0022_mail_message_recipients"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))
            ],
            state_operations=[
                migrations.CreateModel(
                    name="CampaignArchive",
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
                        ("checksum", models.CharField(max_length=64)),
                        (
                            "file_name",
                            models.CharField(blank=True, default="", max_length=255),
                        ),
                        ("file_size", models.PositiveIntegerField()),
                        ("schema_version", models.CharField(max_length=16)),
                        ("campaign_name", models.CharField(max_length=200)),
                        ("campaign_year", models.PositiveSmallIntegerField()),
                        ("is_final", models.BooleanField()),
                        ("as_of", parishkit.stewardship.storage.UTCDateTimeField()),
                        ("timezone", models.CharField(max_length=64)),
                        ("start_date", models.DateField()),
                        ("last_submission_date", models.DateField()),
                        ("active_families", models.PositiveIntegerField()),
                        ("active_families_provenance", models.TextField()),
                        ("source_system", models.TextField()),
                        ("dedup_rule", models.TextField()),
                        ("family_id_systems", models.JSONField()),
                        ("family_rows_kept", models.BooleanField()),
                        ("notes", models.JSONField()),
                        ("days", models.PositiveIntegerField()),
                        ("families_responded", models.PositiveIntegerField()),
                        ("submission_rows", models.PositiveIntegerField()),
                        ("unattributed_rows", models.PositiveIntegerField()),
                        ("ministry_rows", models.PositiveIntegerField()),
                        ("pledging_families", models.PositiveIntegerField()),
                        (
                            "annual_pledged",
                            models.DecimalField(decimal_places=2, max_digits=18),
                        ),
                        (
                            "first_quartile",
                            models.DecimalField(
                                decimal_places=2, max_digits=18, null=True
                            ),
                        ),
                        (
                            "median",
                            models.DecimalField(
                                decimal_places=2, max_digits=18, null=True
                            ),
                        ),
                        (
                            "third_quartile",
                            models.DecimalField(
                                decimal_places=2, max_digits=18, null=True
                            ),
                        ),
                        ("band_set_version", models.PositiveSmallIntegerField()),
                        ("state", models.CharField(default="pending", max_length=8)),
                        ("imported_by_id", models.UUIDField(null=True)),
                        (
                            "imported_at",
                            parishkit.stewardship.storage.UTCDateTimeField(null=True),
                        ),
                        ("removed_by_id", models.UUIDField(null=True)),
                        (
                            "removed_at",
                            parishkit.stewardship.storage.UTCDateTimeField(null=True),
                        ),
                    ],
                    options={
                        "db_table": "stewardship_campaign_archive",
                        "abstract": False,
                        "constraints": [
                            models.CheckConstraint(
                                condition=models.Q(("version__gte", 1)),
                                name="stewardship_reports_campaignarchive_positive_version",
                            ),
                            models.UniqueConstraint(
                                condition=models.Q(("state", "active")),
                                fields=("checksum",),
                                name="campaign_archive_active_checksum",
                            ),
                            models.UniqueConstraint(
                                condition=models.Q(("state", "active")),
                                fields=("campaign_year",),
                                name="campaign_archive_active_year",
                            ),
                            models.CheckConstraint(
                                condition=models.Q(
                                    ("state__in", ("pending", "active", "removed"))
                                ),
                                name="campaign_archive_state",
                            ),
                            models.CheckConstraint(
                                condition=models.Q(
                                    ("active_families__gte", 1),
                                    ("annual_pledged__gte", 0),
                                    ("band_set_version__gte", 1),
                                    ("campaign_year__gte", 2000),
                                    ("campaign_year__lte", 2100),
                                    ("checksum__regex", "^[0-9a-f]{64}$"),
                                    ("days__gte", 1),
                                    ("file_size__gte", 1),
                                    ("file_size__lte", 20971520),
                                    (
                                        "last_submission_date__gte",
                                        models.F("start_date"),
                                    ),
                                    (
                                        "pledging_families__lte",
                                        models.F("families_responded"),
                                    ),
                                ),
                                name="campaign_archive_values",
                            ),
                            models.CheckConstraint(
                                condition=models.Q(
                                    models.Q(
                                        ("first_quartile__isnull", True),
                                        ("median__isnull", True),
                                        ("third_quartile__isnull", True),
                                    ),
                                    models.Q(
                                        ("first_quartile__isnull", False),
                                        ("first_quartile__lte", models.F("median")),
                                        ("median__isnull", False),
                                        ("median__lte", models.F("third_quartile")),
                                        ("third_quartile__isnull", False),
                                    ),
                                    _connector="OR",
                                ),
                                name="campaign_archive_quartiles",
                            ),
                            models.CheckConstraint(
                                condition=models.Q(
                                    models.Q(
                                        ("imported_at__isnull", True),
                                        ("imported_by_id__isnull", True),
                                        ("removed_at__isnull", True),
                                        ("removed_by_id__isnull", True),
                                        ("state", "pending"),
                                    ),
                                    models.Q(
                                        ("imported_at__isnull", False),
                                        ("imported_by_id__isnull", False),
                                        ("removed_at__isnull", True),
                                        ("removed_by_id__isnull", True),
                                        ("state", "active"),
                                    ),
                                    models.Q(
                                        ("imported_at__isnull", False),
                                        ("imported_by_id__isnull", False),
                                        ("removed_at__isnull", False),
                                        ("removed_by_id__isnull", False),
                                        ("state", "removed"),
                                    ),
                                    _connector="OR",
                                ),
                                name="campaign_archive_state_attribution",
                            ),
                        ],
                    },
                ),
                migrations.CreateModel(
                    name="CampaignArchiveBand",
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
                        ("band_set_version", models.PositiveSmallIntegerField()),
                        ("band", models.PositiveSmallIntegerField()),
                        ("families", models.PositiveIntegerField()),
                        ("total", models.DecimalField(decimal_places=2, max_digits=18)),
                        (
                            "archive",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.PROTECT,
                                related_name="band_rows",
                                to="stewardship_reports.campaignarchive",
                            ),
                        ),
                    ],
                    options={
                        "db_table": "stewardship_campaign_archive_band",
                        "constraints": [
                            models.UniqueConstraint(
                                fields=("archive", "band_set_version", "band"),
                                name="campaign_archive_band_unique",
                            ),
                            models.CheckConstraint(
                                condition=models.Q(
                                    ("band__gte", 1),
                                    ("band_set_version__gte", 1),
                                    ("total__gte", 0),
                                ),
                                name="campaign_archive_band_values",
                            ),
                        ],
                    },
                ),
                migrations.CreateModel(
                    name="CampaignArchiveDay",
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
                        ("day", models.PositiveIntegerField()),
                        ("local_date", models.DateField()),
                        ("first_submissions", models.PositiveIntegerField()),
                        ("cumulative_families", models.PositiveIntegerField()),
                        ("cumulative_pledging_families", models.PositiveIntegerField()),
                        (
                            "cumulative_annual_pledged",
                            models.DecimalField(decimal_places=2, max_digits=18),
                        ),
                        (
                            "archive",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.PROTECT,
                                related_name="day_rows",
                                to="stewardship_reports.campaignarchive",
                            ),
                        ),
                    ],
                    options={
                        "db_table": "stewardship_campaign_archive_day",
                        "constraints": [
                            models.UniqueConstraint(
                                fields=("archive", "day"),
                                name="campaign_archive_day_unique",
                            ),
                            models.CheckConstraint(
                                condition=models.Q(
                                    ("cumulative_annual_pledged__gte", 0),
                                    (
                                        "cumulative_pledging_families__lte",
                                        models.F("cumulative_families"),
                                    ),
                                    ("day__gte", 1),
                                ),
                                name="campaign_archive_day_values",
                            ),
                        ],
                    },
                ),
                migrations.CreateModel(
                    name="CampaignArchiveFamily",
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
                        ("family_id_system", models.CharField(max_length=16)),
                        ("family_id", models.PositiveBigIntegerField()),
                        ("first_campaign_day", models.PositiveIntegerField()),
                        (
                            "archive",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.PROTECT,
                                related_name="family_rows",
                                to="stewardship_reports.campaignarchive",
                            ),
                        ),
                    ],
                    options={
                        "db_table": "stewardship_campaign_archive_family",
                        "constraints": [
                            models.UniqueConstraint(
                                fields=("archive", "family_id_system", "family_id"),
                                name="campaign_archive_family_unique",
                            ),
                            models.CheckConstraint(
                                condition=models.Q(
                                    ("family_id__gte", 1),
                                    (
                                        "family_id_system__in",
                                        ("parishsoft_duid", "pds_fid"),
                                    ),
                                    ("first_campaign_day__gte", 1),
                                ),
                                name="campaign_archive_family_values",
                            ),
                        ],
                    },
                ),
            ],
        ),
    ]
