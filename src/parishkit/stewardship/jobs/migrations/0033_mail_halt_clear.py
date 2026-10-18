"""Forward migration: clear a halted mail sender, server side (ADM-13 PR 4a, #530).

Its SQL is the frozen file ``schema/migrations/0115_mail_halt_clear.sql``
(prefix 0115 in the repository-wide file sequence), read whole and never
parsed. It creates ``stewardship_mail_halt`` (one halt, recorded by mail
dispatch), ``stewardship_mail_halt_clear`` (an Administrator's clear signal
for one halt, recorded by the web) and ``stewardship_mailbox_check`` (an
Administrator's mailbox sign-in check, requested by the web and settled by
mail dispatch), each with its checks, index and invoker guard trigger. None
needs a recent sign-in (#1547), and no SECURITY DEFINER routine is created.
It ends with a DO block that refuses to commit unless all of it is installed
as declared. The state operations record the models in Django's model state,
so ``makemigrations --check`` stays clean. Reversing needs its own forward
migration.

It follows the users swap (#952), so its tables are granted only once it is
applied (runtime_grants.CLOSURE_TABLES).
"""

import uuid
from pathlib import Path

import django.db.models.deletion
import django.db.models.functions.datetime
from django.db import migrations, models

import parishkit.stewardship.storage

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0115_mail_halt_clear.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The jobs app's previous migration, and the migration that installs
        # the previous frozen file (0114, no sign-in age, #1547, which merges
        # just before this one), so the files apply in prefix order.
        ("stewardship_jobs", "0032_debug_off_clear_subject"),
        ("stewardship_accounts", "0027_no_sign_in_age"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))
            ],
            state_operations=[
                migrations.CreateModel(
                    name="MailHalt",
                    fields=[
                        (
                            "id",
                            models.UUIDField(
                                editable=False, primary_key=True, serialize=False
                            ),
                        ),
                        (
                            "began_at",
                            parishkit.stewardship.storage.UTCDateTimeField(
                                db_default=django.db.models.functions.datetime.Now()
                            ),
                        ),
                        ("kind", models.CharField(max_length=16)),
                    ],
                    options={
                        "db_table": "stewardship_mail_halt",
                        "indexes": [
                            models.Index(
                                models.OrderBy(models.F("began_at"), descending=True),
                                name="mail_halt_newest",
                            )
                        ],
                        "constraints": [
                            models.CheckConstraint(
                                condition=models.Q(
                                    kind__in=("before_data", "after_data")
                                ),
                                name="mail_halt_kind",
                            )
                        ],
                    },
                ),
                migrations.CreateModel(
                    name="MailHaltClear",
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
                                db_default=django.db.models.functions.datetime.Now()
                            ),
                        ),
                        ("actor_id", models.UUIDField()),
                        ("session_id", models.UUIDField()),
                        (
                            "halt",
                            models.OneToOneField(
                                on_delete=django.db.models.deletion.PROTECT,
                                related_name="clear",
                                to="stewardship_jobs.mailhalt",
                            ),
                        ),
                    ],
                    options={"db_table": "stewardship_mail_halt_clear"},
                ),
                migrations.CreateModel(
                    name="MailboxCheck",
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
                                db_default=django.db.models.functions.datetime.Now()
                            ),
                        ),
                        ("actor_id", models.UUIDField()),
                        ("session_id", models.UUIDField()),
                        (
                            "state",
                            models.CharField(
                                db_default="waiting", default="waiting", max_length=16
                            ),
                        ),
                        (
                            "claimed_at",
                            parishkit.stewardship.storage.UTCDateTimeField(null=True),
                        ),
                        (
                            "finished_at",
                            parishkit.stewardship.storage.UTCDateTimeField(null=True),
                        ),
                    ],
                    options={
                        "db_table": "stewardship_mailbox_check",
                        "indexes": [
                            models.Index(
                                models.OrderBy(models.F("created_at"), descending=True),
                                name="mailbox_check_newest",
                            )
                        ],
                        "constraints": [
                            models.CheckConstraint(
                                condition=models.Q(
                                    state__in=(
                                        "waiting",
                                        "running",
                                        "passed",
                                        "refused",
                                        "unavailable",
                                    )
                                ),
                                name="mailbox_check_state",
                            ),
                            models.CheckConstraint(
                                condition=models.Q(
                                    claimed_at__isnull=True,
                                    finished_at__isnull=True,
                                    state="waiting",
                                )
                                | models.Q(
                                    claimed_at__isnull=False,
                                    finished_at__isnull=True,
                                    state="running",
                                )
                                | models.Q(
                                    claimed_at__isnull=False,
                                    finished_at__isnull=False,
                                    state__in=("passed", "refused", "unavailable"),
                                ),
                                name="mailbox_check_shape",
                            ),
                        ],
                    },
                ),
            ],
        ),
    ]
