"""Forward migration: Ministry leader emails, sent by hand (#1592).

Its SQL is the frozen file ``schema/migrations/0117_leader_emails.sql``
(prefix 0117 in the repository-wide file sequence; provisional until the
files before it, 0114 to 0116, are merged), read whole and never parsed. It
creates the request, cohort and recipient tables with their guards, the
facts function and the Users page's review read, the preparation and
dispatch admissions of the new outbox purpose ``leader_email``, adds the
purpose to the outbox's checks and the ``leader_email_prepare`` Task type to
the two task-type maps, and replaces the four outbox guards that name every
owner. It ends with a DO block that refuses to commit unless all of it is
installed as declared. The state operations record the models and the
outbox checks in Django's model state, so ``makemigrations --check`` stays
clean. Reversing needs its own forward migration.

It follows the users swap (#952), so its tables and the review read are
granted only once it is applied (runtime_grants.CLOSURE_TABLES and
CLOSURE_FUNCTIONS).
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
    / "0117_leader_emails.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The jobs app's previous migration.
        ("stewardship_jobs", "0032_debug_off_clear_subject"),
        # The migration that installs the previous frozen file (0113, the
        # census handling registry, #1596), so the files apply in prefix
        # order; 0114 to 0116 belong to open pull requests, and this names
        # the migration that installs 0116 once they merge. It follows the
        # users swap, so `migrate --users-upgrade-base` never applies it.
        ("stewardship_responses", "0011_census_handling_registry"),
        ("stewardship_accounts", "0026_setup_slack_off"),
        ("stewardship_campaigns", "0013_any_creation_timezone"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))
            ],
            state_operations=[
                migrations.CreateModel(
                    name="LeaderEmailCohort",
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
                        ("mode", models.CharField(max_length=16)),
                        (
                            "testing_recipient",
                            models.CharField(max_length=254, null=True),
                        ),
                        ("addresses", models.JSONField()),
                        ("recipient_count", models.PositiveIntegerField()),
                        ("fence", models.PositiveBigIntegerField()),
                        ("worker_id", models.UUIDField()),
                    ],
                    options={
                        "db_table": "stewardship_leader_email_cohort",
                    },
                ),
                migrations.CreateModel(
                    name="LeaderEmailRecipient",
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
                        ("address", models.CharField(max_length=254)),
                        ("subject", models.CharField(max_length=254)),
                        ("html", models.TextField()),
                        ("text", models.TextField()),
                    ],
                    options={
                        "db_table": "stewardship_leader_email_recipient",
                    },
                ),
                migrations.CreateModel(
                    name="LeaderEmailRequest",
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
                        ("mode", models.CharField(max_length=16)),
                        (
                            "testing_recipient",
                            models.CharField(max_length=254, null=True),
                        ),
                        ("addresses", models.JSONField()),
                        ("recipients", models.JSONField()),
                        ("recipient_count", models.PositiveIntegerField()),
                    ],
                    options={
                        "db_table": "stewardship_leader_email_request",
                    },
                ),
                migrations.RemoveConstraint(
                    model_name="outboxmessage",
                    name="outbox_routing",
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
                                    "leader_email",
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
                                ("family", None),
                                ("purpose__in", ("operational", "security_event")),
                                ("routing", "operational"),
                            ),
                            models.Q(
                                ("campaign", None),
                                ("family", None),
                                ("purpose", "leader_email"),
                                models.Q(
                                    models.Q(
                                        ("mode", "testing"),
                                        ("routing", "testing_override"),
                                    ),
                                    models.Q(
                                        ("mode", "production"),
                                        ("routing", "production"),
                                    ),
                                    _connector="OR",
                                ),
                            ),
                            models.Q(
                                models.Q(
                                    (
                                        "purpose__in",
                                        (
                                            "operational",
                                            "security_event",
                                            "leader_email",
                                        ),
                                    ),
                                    _negated=True,
                                ),
                                ("campaign__isnull", False),
                                models.Q(
                                    models.Q(
                                        ("mode", "testing"),
                                        ("routing", "testing_override"),
                                    ),
                                    models.Q(
                                        ("mode", "production"),
                                        ("routing", "production"),
                                    ),
                                    _connector="OR",
                                ),
                            ),
                            _connector="OR",
                        ),
                        name="outbox_routing",
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
                                        "leader_email",
                                    ],
                                ),
                            ),
                            _connector="OR",
                        ),
                        name="outbox_family_purpose",
                    ),
                ),
                migrations.AddField(
                    model_name="leaderemailcohort",
                    name="configuration",
                    field=models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="stewardship_accounts.appliedconfigurationversion",
                    ),
                ),
                migrations.AddField(
                    model_name="leaderemailcohort",
                    name="parish",
                    field=models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="stewardship_accounts.parish",
                    ),
                ),
                migrations.AddField(
                    model_name="leaderemailcohort",
                    name="run",
                    field=models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="stewardship_jobs.taskrun",
                    ),
                ),
                migrations.AddField(
                    model_name="leaderemailrecipient",
                    name="cohort",
                    field=models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="stewardship_jobs.leaderemailcohort",
                    ),
                ),
                migrations.AddField(
                    model_name="leaderemailrecipient",
                    name="outbox",
                    field=models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="stewardship_jobs.outboxmessage",
                    ),
                ),
                migrations.AddField(
                    model_name="leaderemailrequest",
                    name="campaign",
                    field=models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="stewardship_campaigns.campaign",
                    ),
                ),
                migrations.AddField(
                    model_name="leaderemailrequest",
                    name="configuration",
                    field=models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="stewardship_accounts.appliedconfigurationversion",
                    ),
                ),
                migrations.AddField(
                    model_name="leaderemailcohort",
                    name="request",
                    field=models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="stewardship_jobs.leaderemailrequest",
                    ),
                ),
                migrations.AddConstraint(
                    model_name="leaderemailrecipient",
                    constraint=models.UniqueConstraint(
                        fields=("cohort", "address"),
                        name="leader_email_recipient_address",
                    ),
                ),
                migrations.AddConstraint(
                    model_name="leaderemailrequest",
                    constraint=models.CheckConstraint(
                        condition=models.Q(
                            models.Q(
                                ("mode", "production"), ("testing_recipient", None)
                            ),
                            models.Q(
                                ("mode", "testing"),
                                ("testing_recipient__isnull", False),
                            ),
                            _connector="OR",
                        ),
                        name="leader_email_request_mode",
                    ),
                ),
                migrations.AddConstraint(
                    model_name="leaderemailrequest",
                    constraint=models.CheckConstraint(
                        condition=models.Q(
                            ("recipient_count__gte", 1), ("recipient_count__lte", 100)
                        ),
                        name="leader_email_request_count",
                    ),
                ),
                migrations.AddConstraint(
                    model_name="leaderemailcohort",
                    constraint=models.CheckConstraint(
                        condition=models.Q(("mode__in", ("testing", "production"))),
                        name="leader_email_cohort_mode",
                    ),
                ),
                migrations.AddConstraint(
                    model_name="leaderemailcohort",
                    constraint=models.CheckConstraint(
                        condition=models.Q(
                            ("fence__gte", 1), ("recipient_count__gte", 1)
                        ),
                        name="leader_email_cohort_counts",
                    ),
                ),
            ],
        )
    ]
