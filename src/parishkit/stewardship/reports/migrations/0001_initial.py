import uuid

import django.db.models.deletion
import django.db.models.functions.datetime
from django.db import migrations, models

import parishkit.stewardship.observability
import parishkit.stewardship.storage


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("stewardship_campaigns", "0032_session_epoch_and_instant_guards"),
        ("stewardship_jobs", "0002_taskrun_guards"),
        ("stewardship_source", "0007_compaction_evidence_guard"),
    ]

    operations = [
        migrations.CreateModel(
            name="CampaignDailyFactSet",
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
                ("actor_id", models.UUIDField(blank=True, editable=False, null=True)),
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
                ("version", models.PositiveBigIntegerField(default=1, editable=False)),
                ("population_scope", models.CharField(max_length=12)),
                ("source_generation", models.PositiveBigIntegerField()),
                ("submission_watermark", models.PositiveBigIntegerField()),
                ("through_date", models.DateField()),
                ("first_date", models.DateField(blank=True, null=True)),
                ("last_date", models.DateField(blank=True, null=True)),
                ("expected_count", models.PositiveIntegerField()),
                ("state", models.CharField(default="building", max_length=8)),
                ("task_fence", models.PositiveBigIntegerField()),
                ("worker_id", models.UUIDField()),
                (
                    "ready_at",
                    parishkit.stewardship.storage.UTCDateTimeField(
                        blank=True, null=True
                    ),
                ),
                ("failure_code", models.CharField(blank=True, max_length=48)),
                (
                    "campaign",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="stewardship_campaigns.campaign",
                    ),
                ),
                (
                    "source",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="stewardship_source.sourcesnapshot",
                    ),
                ),
                (
                    "task",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="stewardship_jobs.taskrun",
                    ),
                ),
                (
                    "timezone_configuration",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="stewardship_campaigns.campaignconfiguration",
                    ),
                ),
            ],
            options={
                "db_table": "stewardship_daily_fact_set",
                "abstract": False,
            },
        ),
        migrations.CreateModel(
            name="CampaignDailyFact",
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
                ("actor_id", models.UUIDField(blank=True, editable=False, null=True)),
                (
                    "correlation_id",
                    models.UUIDField(
                        db_index=True,
                        default=parishkit.stewardship.observability.current_correlation,
                        editable=False,
                    ),
                ),
                ("local_date", models.DateField()),
                ("first_responses", models.PositiveBigIntegerField()),
                ("cumulative_responses", models.PositiveBigIntegerField()),
                ("cohort_denominator", models.PositiveBigIntegerField()),
                (
                    "source_generation",
                    models.PositiveBigIntegerField(blank=True, null=True),
                ),
                (
                    "source_as_of",
                    parishkit.stewardship.storage.UTCDateTimeField(
                        blank=True, null=True
                    ),
                ),
                ("population_available", models.BooleanField()),
                ("pledge_available", models.BooleanField()),
                (
                    "pledge_total",
                    models.DecimalField(
                        blank=True, decimal_places=2, max_digits=18, null=True
                    ),
                ),
                (
                    "fact_set",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="days",
                        to="stewardship_reports.campaigndailyfactset",
                    ),
                ),
            ],
            options={
                "db_table": "stewardship_daily_fact",
            },
        ),
        migrations.CreateModel(
            name="CampaignFactPin",
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
                ("actor_id", models.UUIDField(blank=True, editable=False, null=True)),
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
                ("version", models.PositiveBigIntegerField(default=1, editable=False)),
                ("parent_kind", models.CharField(max_length=16)),
                ("parent_id", models.UUIDField()),
                (
                    "fact_set",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="stewardship_reports.campaigndailyfactset",
                    ),
                ),
            ],
            options={
                "db_table": "stewardship_fact_pin",
                "abstract": False,
            },
        ),
        migrations.CreateModel(
            name="CampaignFactPointer",
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
                ("actor_id", models.UUIDField(blank=True, editable=False, null=True)),
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
                ("version", models.PositiveBigIntegerField(default=1, editable=False)),
                ("population_scope", models.CharField(max_length=12)),
                (
                    "campaign",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="stewardship_campaigns.campaign",
                    ),
                ),
                (
                    "fact_set",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="stewardship_reports.campaigndailyfactset",
                    ),
                ),
            ],
            options={
                "db_table": "stewardship_fact_pointer",
                "abstract": False,
            },
        ),
        migrations.CreateModel(
            name="CampaignFactRebuildDemand",
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
                ("actor_id", models.UUIDField(blank=True, editable=False, null=True)),
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
                ("version", models.PositiveBigIntegerField(default=1, editable=False)),
                ("population_scope", models.CharField(max_length=12)),
                ("requested_source_generation", models.PositiveBigIntegerField()),
                ("requested_submission_watermark", models.PositiveBigIntegerField()),
                ("requested_through_date", models.DateField()),
                ("pending_revision", models.PositiveBigIntegerField(default=1)),
                (
                    "pending_first_at",
                    parishkit.stewardship.storage.UTCDateTimeField(
                        blank=True, null=True
                    ),
                ),
                (
                    "pending_last_at",
                    parishkit.stewardship.storage.UTCDateTimeField(
                        blank=True, null=True
                    ),
                ),
                (
                    "pending_due_at",
                    parishkit.stewardship.storage.UTCDateTimeField(
                        blank=True, null=True
                    ),
                ),
                ("claimed_revision", models.PositiveBigIntegerField(default=0)),
                (
                    "campaign",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="stewardship_campaigns.campaign",
                    ),
                ),
                (
                    "claimed_generation",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        to="stewardship_reports.campaigndailyfactset",
                    ),
                ),
                (
                    "claimed_task",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        to="stewardship_jobs.taskrun",
                    ),
                ),
                (
                    "requested_source",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="stewardship_source.sourcesnapshot",
                    ),
                ),
                (
                    "requested_timezone_configuration",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="stewardship_campaigns.campaignconfiguration",
                    ),
                ),
            ],
            options={
                "db_table": "stewardship_fact_demand",
                "abstract": False,
            },
        ),
        migrations.AddIndex(
            model_name="campaigndailyfactset",
            index=models.Index(
                fields=["campaign", "population_scope", "state"],
                name="daily_fact_lookup",
            ),
        ),
        migrations.AddConstraint(
            model_name="campaigndailyfactset",
            constraint=models.CheckConstraint(
                condition=models.Q(("version__gte", 1)),
                name="stewardship_reports_campaigndailyfactset_positive_version",
            ),
        ),
        migrations.AddConstraint(
            model_name="campaigndailyfactset",
            constraint=models.UniqueConstraint(
                fields=(
                    "campaign",
                    "population_scope",
                    "source_generation",
                    "submission_watermark",
                    "timezone_configuration",
                    "through_date",
                ),
                name="daily_fact_exact_inputs",
            ),
        ),
        migrations.AddConstraint(
            model_name="campaigndailyfactset",
            constraint=models.CheckConstraint(
                condition=models.Q(("population_scope__in", ("historical", "current"))),
                name="daily_fact_scope",
            ),
        ),
        migrations.AddConstraint(
            model_name="campaigndailyfactset",
            constraint=models.CheckConstraint(
                condition=models.Q(("state__in", ("building", "ready", "failed"))),
                name="daily_fact_state",
            ),
        ),
        migrations.AddConstraint(
            model_name="campaigndailyfactset",
            constraint=models.CheckConstraint(
                condition=models.Q(("source_generation__gt", 0), ("task_fence__gt", 0)),
                name="daily_fact_positive_fences",
            ),
        ),
        migrations.AddConstraint(
            model_name="campaigndailyfactset",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(
                        ("expected_count", 0),
                        ("first_date__isnull", True),
                        ("last_date__isnull", True),
                    ),
                    models.Q(
                        ("expected_count__gt", 0),
                        ("first_date__isnull", False),
                        ("last_date__gte", models.F("first_date")),
                        ("last_date__isnull", False),
                    ),
                    _connector="OR",
                ),
                name="daily_fact_expected_dates",
            ),
        ),
        migrations.AddConstraint(
            model_name="campaigndailyfactset",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(("ready_at__isnull", False), ("state", "ready")),
                    models.Q(
                        models.Q(("state", "ready"), _negated=True),
                        ("ready_at__isnull", True),
                    ),
                    _connector="OR",
                ),
                name="daily_fact_ready_instant",
            ),
        ),
        migrations.AddConstraint(
            model_name="campaigndailyfactset",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("failure_code", ""),
                    ("failure_code__regex", "^[a-z][a-z0-9_]{0,47}$"),
                    _connector="OR",
                ),
                name="daily_fact_failure_code",
            ),
        ),
        migrations.AddConstraint(
            model_name="campaigndailyfact",
            constraint=models.UniqueConstraint(
                fields=("fact_set", "local_date"), name="daily_fact_date"
            ),
        ),
        migrations.AddConstraint(
            model_name="campaigndailyfact",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("cumulative_responses__lte", models.F("cohort_denominator")),
                    ("first_responses__lte", models.F("cumulative_responses")),
                ),
                name="daily_fact_count_bounds",
            ),
        ),
        migrations.AddConstraint(
            model_name="campaigndailyfact",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(
                        ("cohort_denominator", 0),
                        ("cumulative_responses", 0),
                        ("first_responses", 0),
                        ("population_available", False),
                        ("source_as_of__isnull", True),
                        ("source_generation__isnull", True),
                    ),
                    models.Q(
                        ("population_available", True),
                        ("source_as_of__isnull", False),
                        ("source_generation__gt", 0),
                        ("source_generation__isnull", False),
                    ),
                    _connector="OR",
                ),
                name="daily_fact_source_availability",
            ),
        ),
        migrations.AddConstraint(
            model_name="campaigndailyfact",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(
                        ("pledge_available", False), ("pledge_total__isnull", True)
                    ),
                    models.Q(
                        ("pledge_available", True),
                        ("pledge_total__gte", 0),
                        ("pledge_total__isnull", False),
                        ("pledge_total__lte", "9999999999999999.99"),
                        ("population_available", True),
                    ),
                    _connector="OR",
                ),
                name="daily_fact_pledge_availability",
            ),
        ),
        migrations.AddConstraint(
            model_name="campaignfactpin",
            constraint=models.CheckConstraint(
                condition=models.Q(("version__gte", 1)),
                name="stewardship_reports_campaignfactpin_positive_version",
            ),
        ),
        migrations.AddConstraint(
            model_name="campaignfactpin",
            constraint=models.UniqueConstraint(
                fields=("fact_set", "parent_kind", "parent_id"), name="fact_pin_parent"
            ),
        ),
        migrations.AddConstraint(
            model_name="campaignfactpin",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    (
                        "parent_kind__in",
                        (
                            "export",
                            "digest",
                            "render",
                            "verification",
                            "work",
                            "operator",
                        ),
                    )
                ),
                name="fact_pin_kind",
            ),
        ),
        migrations.AddConstraint(
            model_name="campaignfactpointer",
            constraint=models.CheckConstraint(
                condition=models.Q(("version__gte", 1)),
                name="stewardship_reports_campaignfactpointer_positive_version",
            ),
        ),
        migrations.AddConstraint(
            model_name="campaignfactpointer",
            constraint=models.UniqueConstraint(
                fields=("campaign", "population_scope"), name="fact_pointer_scope"
            ),
        ),
        migrations.AddConstraint(
            model_name="campaignfactpointer",
            constraint=models.CheckConstraint(
                condition=models.Q(("population_scope__in", ("historical", "current"))),
                name="fact_pointer_known_scope",
            ),
        ),
        migrations.AddIndex(
            model_name="campaignfactrebuilddemand",
            index=models.Index(fields=["pending_due_at"], name="fact_demand_due"),
        ),
        migrations.AddConstraint(
            model_name="campaignfactrebuilddemand",
            constraint=models.CheckConstraint(
                condition=models.Q(("version__gte", 1)),
                name="stewardship_reports_campaignfactrebuilddemand_positive_version",
            ),
        ),
        migrations.AddConstraint(
            model_name="campaignfactrebuilddemand",
            constraint=models.UniqueConstraint(
                fields=("campaign", "population_scope"), name="fact_demand_scope"
            ),
        ),
        migrations.AddConstraint(
            model_name="campaignfactrebuilddemand",
            constraint=models.CheckConstraint(
                condition=models.Q(("population_scope__in", ("historical", "current"))),
                name="fact_demand_known_scope",
            ),
        ),
        migrations.AddConstraint(
            model_name="campaignfactrebuilddemand",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("pending_revision__gte", models.F("claimed_revision")),
                    ("requested_source_generation__gt", 0),
                ),
                name="fact_demand_revision_order",
            ),
        ),
        migrations.AddConstraint(
            model_name="campaignfactrebuilddemand",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(
                        ("pending_due_at__isnull", True),
                        ("pending_first_at__isnull", True),
                        ("pending_last_at__isnull", True),
                    ),
                    models.Q(
                        ("pending_due_at__gte", models.F("pending_first_at")),
                        ("pending_due_at__isnull", False),
                        ("pending_first_at__isnull", False),
                        ("pending_last_at__gte", models.F("pending_first_at")),
                        ("pending_last_at__isnull", False),
                    ),
                    _connector="OR",
                ),
                name="fact_demand_window_shape",
            ),
        ),
        migrations.AddConstraint(
            model_name="campaignfactrebuilddemand",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(
                        ("claimed_generation__isnull", True),
                        ("claimed_task__isnull", True),
                    ),
                    models.Q(
                        ("claimed_generation__isnull", False),
                        ("claimed_revision__gt", 0),
                        ("claimed_task__isnull", False),
                    ),
                    _connector="OR",
                ),
                name="fact_demand_claim_shape",
            ),
        ),
    ]
