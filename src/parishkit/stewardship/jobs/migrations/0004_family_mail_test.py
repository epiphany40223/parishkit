import uuid

import django.db.models.deletion
import django.db.models.functions.datetime
from django.db import migrations, models

import parishkit.stewardship.observability
import parishkit.stewardship.storage


class Migration(migrations.Migration):
    # Baseline SQL owns creation; this step only resolves the model state.

    dependencies = [
        ("stewardship_accounts", "0003_initial"),
        ("stewardship_campaigns", "0001_initial"),
        ("stewardship_jobs", "0003_backup_run"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.CreateModel(
                    name="FamilyMailTest",
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
                        ("requested_by_id", models.UUIDField()),
                        ("request_key", models.UUIDField()),
                        ("sequence", models.PositiveBigIntegerField()),
                        (
                            "reauthenticated_at",
                            parishkit.stewardship.storage.UTCDateTimeField(),
                        ),
                        ("rehearsal_epoch_id", models.UUIDField()),
                        ("family_id", models.UUIDField(null=True)),
                        ("outbox_id", models.UUIDField(null=True)),
                        (
                            "state",
                            models.CharField(
                                db_default="queued", default="queued", max_length=16
                            ),
                        ),
                        (
                            "campaign",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.PROTECT,
                                to="stewardship_campaigns.campaign",
                            ),
                        ),
                        (
                            "configuration",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.PROTECT,
                                to="stewardship_accounts.appliedconfigurationversion",
                            ),
                        ),
                        (
                            "task",
                            models.OneToOneField(
                                on_delete=django.db.models.deletion.PROTECT,
                                to="stewardship_jobs.taskrun",
                            ),
                        ),
                        (
                            "template",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.PROTECT,
                                to="stewardship_accounts.contentversion",
                            ),
                        ),
                    ],
                    options={
                        "db_table": "stewardship_family_mail_test",
                        "abstract": False,
                    },
                ),
                migrations.AddConstraint(
                    model_name="familymailtest",
                    constraint=models.CheckConstraint(
                        condition=models.Q(("version__gte", 1)),
                        name="stewardship_jobs_familymailtest_positive_version",
                    ),
                ),
                migrations.AddConstraint(
                    model_name="familymailtest",
                    constraint=models.UniqueConstraint(
                        fields=("requested_by_id", "request_key", "sequence"),
                        name="family_mail_test_request",
                    ),
                ),
                migrations.AddConstraint(
                    model_name="familymailtest",
                    constraint=models.CheckConstraint(
                        condition=models.Q(("sequence__gte", 1), ("sequence__lte", 10)),
                        name="family_mail_test_sequence",
                    ),
                ),
                migrations.AddConstraint(
                    model_name="familymailtest",
                    constraint=models.CheckConstraint(
                        condition=models.Q(
                            ("state__in", ("queued", "prepared", "cancelled", "failed"))
                        ),
                        name="family_mail_test_known_state",
                    ),
                ),
                migrations.AddConstraint(
                    model_name="familymailtest",
                    constraint=models.CheckConstraint(
                        condition=models.Q(
                            models.Q(
                                ("family_id__isnull", False),
                                ("outbox_id", None),
                                ("state", "queued"),
                            ),
                            models.Q(
                                ("family_id", None),
                                ("outbox_id__isnull", False),
                                ("state", "prepared"),
                            ),
                            models.Q(
                                ("family_id", None),
                                ("outbox_id", None),
                                ("state__in", ["cancelled", "failed"]),
                            ),
                            _connector="OR",
                        ),
                        name="family_mail_test_family_scrub",
                    ),
                ),
                migrations.RemoveConstraint(
                    model_name="outboxmessage",
                    name="outbox_purpose",
                ),
                migrations.RemoveConstraint(
                    model_name="outboxmessage",
                    name="outbox_family_purpose",
                ),
                migrations.AddConstraint(
                    model_name="outboxmessage",
                    constraint=models.CheckConstraint(
                        condition=models.Q(
                            (
                                "purpose__in",
                                (
                                    "initial",
                                    "reminder",
                                    "receipt",
                                    "family_test",
                                    "daily_digest",
                                    "weekly_digest",
                                    "operational",
                                    "security_event",
                                ),
                            )
                        ),
                        name="outbox_purpose",
                    ),
                ),
                migrations.AddConstraint(
                    model_name="outboxmessage",
                    constraint=models.CheckConstraint(
                        condition=models.Q(
                            models.Q(
                                ("family__isnull", False),
                                (
                                    "purpose__in",
                                    ["initial", "reminder", "receipt", "family_test"],
                                ),
                            ),
                            models.Q(
                                ("family", None),
                                (
                                    "purpose__in",
                                    [
                                        "daily_digest",
                                        "weekly_digest",
                                        "operational",
                                        "security_event",
                                    ],
                                ),
                            ),
                            _connector="OR",
                        ),
                        name="outbox_family_purpose",
                    ),
                ),
                migrations.AddConstraint(
                    model_name="outboxmessage",
                    constraint=models.CheckConstraint(
                        condition=models.Q(
                            models.Q(("purpose", "family_test"), _negated=True),
                            models.Q(
                                ("credential_namespace", "rehearsal"),
                                ("mode", "testing"),
                            ),
                            _connector="OR",
                        ),
                        name="outbox_family_test_testing",
                    ),
                ),
            ]
        )
    ]
