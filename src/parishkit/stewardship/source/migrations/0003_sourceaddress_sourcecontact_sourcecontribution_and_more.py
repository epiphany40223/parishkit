import uuid

import django.db.models.deletion
import django.db.models.functions.datetime
from django.db import migrations, models

import parishkit.stewardship.observability
import parishkit.stewardship.storage


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_jobs", "0002_taskrun_guards"),
        ("stewardship_source", "0002_source_lease_guards"),
    ]

    operations = [
        migrations.CreateModel(
            name="SourceAddress",
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
                ("organization_id", models.PositiveBigIntegerField()),
                ("source_key", models.CharField(max_length=200)),
                ("digest", models.CharField(max_length=64)),
                ("canonical", models.TextField()),
                ("owner_kind", models.CharField(max_length=8)),
                ("owner_key", models.CharField(db_index=True, max_length=200)),
            ],
            options={
                "db_table": "stewardship_source_address",
                "abstract": False,
                "constraints": [
                    models.UniqueConstraint(
                        fields=("organization_id", "source_key", "digest"),
                        name="sourceaddress_identity_digest",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            ("organization_id__gt", 0),
                            models.Q(("source_key", ""), _negated=True),
                            ("digest__regex", "^[0-9a-f]{64}$"),
                        ),
                        name="sourceaddress_valid_identity",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("owner_kind__in", ("family", "member"))),
                        name="source_address_owner_kind",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="SourceContact",
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
                ("organization_id", models.PositiveBigIntegerField()),
                ("source_key", models.CharField(max_length=200)),
                ("digest", models.CharField(max_length=64)),
                ("canonical", models.TextField()),
                ("owner_kind", models.CharField(max_length=8)),
                ("owner_key", models.CharField(db_index=True, max_length=200)),
            ],
            options={
                "db_table": "stewardship_source_contact",
                "abstract": False,
                "constraints": [
                    models.UniqueConstraint(
                        fields=("organization_id", "source_key", "digest"),
                        name="sourcecontact_identity_digest",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            ("organization_id__gt", 0),
                            models.Q(("source_key", ""), _negated=True),
                            ("digest__regex", "^[0-9a-f]{64}$"),
                        ),
                        name="sourcecontact_valid_identity",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("owner_kind__in", ("family", "member"))),
                        name="source_contact_owner_kind",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="SourceContribution",
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
                ("organization_id", models.PositiveBigIntegerField()),
                ("source_key", models.CharField(max_length=200)),
                ("digest", models.CharField(max_length=64)),
                ("canonical", models.TextField()),
                ("family_key", models.CharField(db_index=True, max_length=200)),
                ("fund_key", models.CharField(db_index=True, max_length=200)),
            ],
            options={
                "db_table": "stewardship_source_contribution",
                "abstract": False,
                "constraints": [
                    models.UniqueConstraint(
                        fields=("organization_id", "source_key", "digest"),
                        name="sourcecontribution_identity_digest",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            ("organization_id__gt", 0),
                            models.Q(("source_key", ""), _negated=True),
                            ("digest__regex", "^[0-9a-f]{64}$"),
                        ),
                        name="sourcecontribution_valid_identity",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="SourceFamily",
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
                ("organization_id", models.PositiveBigIntegerField()),
                ("source_key", models.CharField(max_length=200)),
                ("digest", models.CharField(max_length=64)),
                ("canonical", models.TextField()),
            ],
            options={
                "db_table": "stewardship_source_family",
                "abstract": False,
                "constraints": [
                    models.UniqueConstraint(
                        fields=("organization_id", "source_key", "digest"),
                        name="sourcefamily_identity_digest",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            ("organization_id__gt", 0),
                            models.Q(("source_key", ""), _negated=True),
                            ("digest__regex", "^[0-9a-f]{64}$"),
                        ),
                        name="sourcefamily_valid_identity",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="SourceFund",
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
                ("organization_id", models.PositiveBigIntegerField()),
                ("source_key", models.CharField(max_length=200)),
                ("digest", models.CharField(max_length=64)),
                ("canonical", models.TextField()),
            ],
            options={
                "db_table": "stewardship_source_fund",
                "abstract": False,
                "constraints": [
                    models.UniqueConstraint(
                        fields=("organization_id", "source_key", "digest"),
                        name="sourcefund_identity_digest",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            ("organization_id__gt", 0),
                            models.Q(("source_key", ""), _negated=True),
                            ("digest__regex", "^[0-9a-f]{64}$"),
                        ),
                        name="sourcefund_valid_identity",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="SourceMember",
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
                ("organization_id", models.PositiveBigIntegerField()),
                ("source_key", models.CharField(max_length=200)),
                ("digest", models.CharField(max_length=64)),
                ("canonical", models.TextField()),
                ("family_key", models.CharField(db_index=True, max_length=200)),
            ],
            options={
                "db_table": "stewardship_source_member",
                "abstract": False,
                "constraints": [
                    models.UniqueConstraint(
                        fields=("organization_id", "source_key", "digest"),
                        name="sourcemember_identity_digest",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            ("organization_id__gt", 0),
                            models.Q(("source_key", ""), _negated=True),
                            ("digest__regex", "^[0-9a-f]{64}$"),
                        ),
                        name="sourcemember_valid_identity",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="SourceMinistry",
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
                ("organization_id", models.PositiveBigIntegerField()),
                ("source_key", models.CharField(max_length=200)),
                ("digest", models.CharField(max_length=64)),
                ("canonical", models.TextField()),
            ],
            options={
                "db_table": "stewardship_source_ministry",
                "abstract": False,
                "constraints": [
                    models.UniqueConstraint(
                        fields=("organization_id", "source_key", "digest"),
                        name="sourceministry_identity_digest",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            ("organization_id__gt", 0),
                            models.Q(("source_key", ""), _negated=True),
                            ("digest__regex", "^[0-9a-f]{64}$"),
                        ),
                        name="sourceministry_valid_identity",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="SourcePledge",
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
                ("organization_id", models.PositiveBigIntegerField()),
                ("source_key", models.CharField(max_length=200)),
                ("digest", models.CharField(max_length=64)),
                ("canonical", models.TextField()),
                ("family_key", models.CharField(db_index=True, max_length=200)),
                ("fund_key", models.CharField(db_index=True, max_length=200)),
            ],
            options={
                "db_table": "stewardship_source_pledge",
                "abstract": False,
                "constraints": [
                    models.UniqueConstraint(
                        fields=("organization_id", "source_key", "digest"),
                        name="sourcepledge_identity_digest",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            ("organization_id__gt", 0),
                            models.Q(("source_key", ""), _negated=True),
                            ("digest__regex", "^[0-9a-f]{64}$"),
                        ),
                        name="sourcepledge_valid_identity",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="SourceRoster",
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
                ("organization_id", models.PositiveBigIntegerField()),
                ("source_key", models.CharField(max_length=200)),
                ("digest", models.CharField(max_length=64)),
                ("canonical", models.TextField()),
                ("member_key", models.CharField(db_index=True, max_length=200)),
                ("ministry_key", models.CharField(db_index=True, max_length=200)),
            ],
            options={
                "db_table": "stewardship_source_roster",
                "abstract": False,
                "constraints": [
                    models.UniqueConstraint(
                        fields=("organization_id", "source_key", "digest"),
                        name="sourceroster_identity_digest",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            ("organization_id__gt", 0),
                            models.Q(("source_key", ""), _negated=True),
                            ("digest__regex", "^[0-9a-f]{64}$"),
                        ),
                        name="sourceroster_valid_identity",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="SourceSnapshot",
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
                ("organization_id", models.PositiveBigIntegerField()),
                ("kind", models.CharField(max_length=8)),
                ("state", models.CharField(default="staging", max_length=12)),
                ("source_fence", models.PositiveBigIntegerField()),
                ("started_at", parishkit.stewardship.storage.UTCDateTimeField()),
                (
                    "completed_at",
                    parishkit.stewardship.storage.UTCDateTimeField(
                        blank=True, null=True
                    ),
                ),
                (
                    "promoted_at",
                    parishkit.stewardship.storage.UTCDateTimeField(
                        blank=True, db_index=True, null=True
                    ),
                ),
                (
                    "compacted_at",
                    parishkit.stewardship.storage.UTCDateTimeField(
                        blank=True, null=True
                    ),
                ),
                (
                    "generation",
                    models.PositiveBigIntegerField(blank=True, null=True, unique=True),
                ),
                ("counts", models.JSONField(default=dict)),
                ("validation", models.JSONField(default=dict)),
                ("cursor", models.JSONField(default=dict)),
                ("content_digest", models.CharField(blank=True, max_length=64)),
                (
                    "base",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="successors",
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
            ],
            options={
                "db_table": "stewardship_source_snapshot",
                "abstract": False,
            },
        ),
        migrations.CreateModel(
            name="SourceCurrent",
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
                (
                    "singleton",
                    models.BooleanField(default=True, editable=False, unique=True),
                ),
                ("generation", models.PositiveBigIntegerField(default=0)),
                (
                    "organization_id",
                    models.PositiveBigIntegerField(blank=True, null=True),
                ),
                (
                    "snapshot",
                    models.OneToOneField(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        to="stewardship_source.sourcesnapshot",
                    ),
                ),
            ],
            options={
                "db_table": "stewardship_source_current",
                "abstract": False,
            },
        ),
        migrations.CreateModel(
            name="SnapshotRoster",
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
                ("source_key", models.CharField(max_length=200)),
                (
                    "payload",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="stewardship_source.sourceroster",
                    ),
                ),
                (
                    "snapshot",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="stewardship_source.sourcesnapshot",
                    ),
                ),
            ],
            options={
                "db_table": "stewardship_snapshot_roster",
                "abstract": False,
            },
        ),
        migrations.CreateModel(
            name="SnapshotPledge",
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
                ("source_key", models.CharField(max_length=200)),
                (
                    "payload",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="stewardship_source.sourcepledge",
                    ),
                ),
                (
                    "snapshot",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="stewardship_source.sourcesnapshot",
                    ),
                ),
            ],
            options={
                "db_table": "stewardship_snapshot_pledge",
                "abstract": False,
            },
        ),
        migrations.CreateModel(
            name="SnapshotMinistry",
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
                ("source_key", models.CharField(max_length=200)),
                (
                    "payload",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="stewardship_source.sourceministry",
                    ),
                ),
                (
                    "snapshot",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="stewardship_source.sourcesnapshot",
                    ),
                ),
            ],
            options={
                "db_table": "stewardship_snapshot_ministry",
                "abstract": False,
            },
        ),
        migrations.CreateModel(
            name="SnapshotMember",
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
                ("source_key", models.CharField(max_length=200)),
                (
                    "payload",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="stewardship_source.sourcemember",
                    ),
                ),
                (
                    "snapshot",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="stewardship_source.sourcesnapshot",
                    ),
                ),
            ],
            options={
                "db_table": "stewardship_snapshot_member",
                "abstract": False,
            },
        ),
        migrations.CreateModel(
            name="SnapshotFund",
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
                ("source_key", models.CharField(max_length=200)),
                (
                    "payload",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="stewardship_source.sourcefund",
                    ),
                ),
                (
                    "snapshot",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="stewardship_source.sourcesnapshot",
                    ),
                ),
            ],
            options={
                "db_table": "stewardship_snapshot_fund",
                "abstract": False,
            },
        ),
        migrations.CreateModel(
            name="SnapshotFamily",
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
                ("source_key", models.CharField(max_length=200)),
                (
                    "payload",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="stewardship_source.sourcefamily",
                    ),
                ),
                (
                    "snapshot",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="stewardship_source.sourcesnapshot",
                    ),
                ),
            ],
            options={
                "db_table": "stewardship_snapshot_family",
                "abstract": False,
            },
        ),
        migrations.CreateModel(
            name="SnapshotContribution",
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
                ("source_key", models.CharField(max_length=200)),
                (
                    "payload",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="stewardship_source.sourcecontribution",
                    ),
                ),
                (
                    "snapshot",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="stewardship_source.sourcesnapshot",
                    ),
                ),
            ],
            options={
                "db_table": "stewardship_snapshot_contribution",
                "abstract": False,
            },
        ),
        migrations.CreateModel(
            name="SnapshotContact",
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
                ("source_key", models.CharField(max_length=200)),
                (
                    "payload",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="stewardship_source.sourcecontact",
                    ),
                ),
                (
                    "snapshot",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="stewardship_source.sourcesnapshot",
                    ),
                ),
            ],
            options={
                "db_table": "stewardship_snapshot_contact",
                "abstract": False,
            },
        ),
        migrations.CreateModel(
            name="SnapshotAddress",
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
                ("source_key", models.CharField(max_length=200)),
                (
                    "payload",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="stewardship_source.sourceaddress",
                    ),
                ),
                (
                    "snapshot",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="stewardship_source.sourcesnapshot",
                    ),
                ),
            ],
            options={
                "db_table": "stewardship_snapshot_address",
                "abstract": False,
            },
        ),
        migrations.CreateModel(
            name="SourceSnapshotPin",
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
                ("parent_kind", models.CharField(max_length=32)),
                ("parent_id", models.UUIDField()),
                (
                    "expires_at",
                    parishkit.stewardship.storage.UTCDateTimeField(
                        blank=True, null=True
                    ),
                ),
                (
                    "snapshot",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="stewardship_source.sourcesnapshot",
                    ),
                ),
            ],
            options={
                "db_table": "stewardship_source_pin",
                "abstract": False,
            },
        ),
        migrations.AddConstraint(
            model_name="sourcesnapshot",
            constraint=models.CheckConstraint(
                condition=models.Q(("version__gte", 1)),
                name="stewardship_source_sourcesnapshot_positive_version",
            ),
        ),
        migrations.AddConstraint(
            model_name="sourcesnapshot",
            constraint=models.CheckConstraint(
                condition=models.Q(("organization_id__gt", 0), ("source_fence__gt", 0)),
                name="source_snapshot_identity",
            ),
        ),
        migrations.AddConstraint(
            model_name="sourcesnapshot",
            constraint=models.CheckConstraint(
                condition=models.Q(("kind__in", ("full", "delta"))),
                name="source_snapshot_kind",
            ),
        ),
        migrations.AddConstraint(
            model_name="sourcesnapshot",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("state__in", ("staging", "ready", "rejected", "promoted"))
                ),
                name="source_snapshot_state",
            ),
        ),
        migrations.AddConstraint(
            model_name="sourcesnapshot",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(
                        ("generation__gt", 0),
                        ("promoted_at__isnull", False),
                        ("state", "promoted"),
                    ),
                    models.Q(
                        models.Q(("state", "promoted"), _negated=True),
                        ("generation__isnull", True),
                        ("promoted_at__isnull", True),
                    ),
                    _connector="OR",
                ),
                name="source_snapshot_promotion_shape",
            ),
        ),
        migrations.AddConstraint(
            model_name="sourcesnapshot",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("compacted_at__isnull", True),
                    models.Q(
                        ("compacted_at__gte", models.F("promoted_at")),
                        ("state", "promoted"),
                    ),
                    _connector="OR",
                ),
                name="source_snapshot_compaction_shape",
            ),
        ),
        migrations.AddConstraint(
            model_name="sourcesnapshot",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("content_digest", ""),
                    ("content_digest__regex", "^[0-9a-f]{64}$"),
                    _connector="OR",
                ),
                name="source_snapshot_digest",
            ),
        ),
        migrations.AddConstraint(
            model_name="sourcecurrent",
            constraint=models.CheckConstraint(
                condition=models.Q(("version__gte", 1)),
                name="stewardship_source_sourcecurrent_positive_version",
            ),
        ),
        migrations.AddConstraint(
            model_name="sourcecurrent",
            constraint=models.CheckConstraint(
                condition=models.Q(("singleton", True)), name="source_current_singleton"
            ),
        ),
        migrations.AddConstraint(
            model_name="sourcecurrent",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(
                        ("generation", 0),
                        ("organization_id__isnull", True),
                        ("snapshot__isnull", True),
                    ),
                    models.Q(
                        ("generation__gt", 0),
                        ("organization_id__gt", 0),
                        ("snapshot__isnull", False),
                    ),
                    _connector="OR",
                ),
                name="source_current_shape",
            ),
        ),
        migrations.AddConstraint(
            model_name="snapshotroster",
            constraint=models.UniqueConstraint(
                fields=("snapshot", "source_key"), name="snapshotroster_identity"
            ),
        ),
        migrations.AddConstraint(
            model_name="snapshotpledge",
            constraint=models.UniqueConstraint(
                fields=("snapshot", "source_key"), name="snapshotpledge_identity"
            ),
        ),
        migrations.AddConstraint(
            model_name="snapshotministry",
            constraint=models.UniqueConstraint(
                fields=("snapshot", "source_key"), name="snapshotministry_identity"
            ),
        ),
        migrations.AddConstraint(
            model_name="snapshotmember",
            constraint=models.UniqueConstraint(
                fields=("snapshot", "source_key"), name="snapshotmember_identity"
            ),
        ),
        migrations.AddConstraint(
            model_name="snapshotfund",
            constraint=models.UniqueConstraint(
                fields=("snapshot", "source_key"), name="snapshotfund_identity"
            ),
        ),
        migrations.AddConstraint(
            model_name="snapshotfamily",
            constraint=models.UniqueConstraint(
                fields=("snapshot", "source_key"), name="snapshotfamily_identity"
            ),
        ),
        migrations.AddConstraint(
            model_name="snapshotcontribution",
            constraint=models.UniqueConstraint(
                fields=("snapshot", "source_key"), name="snapshotcontribution_identity"
            ),
        ),
        migrations.AddConstraint(
            model_name="snapshotcontact",
            constraint=models.UniqueConstraint(
                fields=("snapshot", "source_key"), name="snapshotcontact_identity"
            ),
        ),
        migrations.AddConstraint(
            model_name="snapshotaddress",
            constraint=models.UniqueConstraint(
                fields=("snapshot", "source_key"), name="snapshotaddress_identity"
            ),
        ),
        migrations.AddConstraint(
            model_name="sourcesnapshotpin",
            constraint=models.CheckConstraint(
                condition=models.Q(("version__gte", 1)),
                name="stewardship_source_sourcesnapshotpin_positive_version",
            ),
        ),
        migrations.AddConstraint(
            model_name="sourcesnapshotpin",
            constraint=models.UniqueConstraint(
                fields=("snapshot", "parent_kind", "parent_id"),
                name="source_pin_parent",
            ),
        ),
        migrations.AddConstraint(
            model_name="sourcesnapshotpin",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    (
                        "parent_kind__in",
                        (
                            "submission",
                            "report",
                            "digest",
                            "form_baseline",
                            "publication",
                            "audit",
                            "boundary",
                            "restore",
                            "delivery_hold",
                            "operator",
                            "facts",
                        ),
                    )
                ),
                name="source_pin_kind",
            ),
        ),
        migrations.AddConstraint(
            model_name="sourcesnapshotpin",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("expires_at__isnull", True),
                    ("parent_kind", "form_baseline"),
                    _connector="OR",
                ),
                name="source_pin_expiry_owner",
            ),
        ),
    ]
