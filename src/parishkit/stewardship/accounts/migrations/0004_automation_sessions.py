"""Forward migration: durable Admin automation sessions (ADM-11 PR 2, #463).

Its SQL is the frozen file ``schema/migrations/0004_automation_sessions.sql``
(the next prefix in the repository-wide file sequence after source's
``0003_refresh_tick_times.sql``, hence the dependency on that migration), read
whole and never parsed. It creates the four automation tables
(``stewardship_automation_session``, ``stewardship_automation_login``,
``stewardship_automation_notice`` and ``stewardship_automation_notice_ack``)
with their guards, and ``stewardship_automation_fresh_v1``, which the
fresh-gate migration of PR 5 will call. It re-creates, copied verbatim from
the fresh-install baseline as it stands at this release, the objects that
list incident kinds or task types: the ``ops_incident_kind`` constraint
(``operational_incidents.sql``), ``stewardship_ops_incident_state_v1`` (the
web login may observe the automation kinds), ``stewardship_ops_content_v1``
(their fixed text, ``operational_render.sql``) and
``stewardship_task_type_login_v1`` (``automation_maintenance`` runs in the
worker, ``functions.sql``). It ends with a DO block that refuses to commit
unless every object and value is installed.

A digest test pins the frozen file, and a second test checks that the latest
migration's copy of each replaced function still equals the baseline's. The
state operations below describe the new models; the constraint's state change
is ``stewardship_jobs.0005_automation_incident_kinds``. A fresh install runs
the baseline and then every forward migration, ending in the same catalog as
an upgraded database. Like the baseline, it has no reverse operation.
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
    / "0004_automation_sessions.sql"
)

END_REASONS = (
    "logout",
    "revoked_by_owner",
    "revoked_by_administrator",
    "role_lost",
    "user_removed",
    "recovery",
    "restore",
    "revoked_by_operator",
    "host_mismatch",
    "misused",
    "pairing_abandoned",
)
NOTICE_KINDS = (
    "approved",
    "fresh_gated",
    "irreversible",
    "policy_change",
    "refused",
    "ended",
)


def _record_fields():
    """The DurableRecord columns every new record starts with."""
    return [
        (
            "id",
            models.UUIDField(
                default=uuid.uuid4, editable=False, primary_key=True, serialize=False
            ),
        ),
        (
            "created_at",
            parishkit.stewardship.storage.UTCDateTimeField(
                db_default=django.db.models.functions.datetime.Now(), editable=False
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
    ]


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_accounts", "0003_initial"),
        # The migration that installed the previous frozen file, so the
        # files apply in prefix order.
        ("stewardship_source", "0002_refresh_tick_times"),
    ]

    operations = [
        migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8")),
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.CreateModel(
                    name="AutomationSession",
                    fields=[
                        *_record_fields(),
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
                        ("principal_id", models.UUIDField()),
                        ("approving_session_id", models.UUIDField()),
                        (
                            "authenticated_at",
                            parishkit.stewardship.storage.UTCDateTimeField(),
                        ),
                        (
                            "expires_at",
                            parishkit.stewardship.storage.UTCDateTimeField(),
                        ),
                        ("scope", models.CharField(max_length=9)),
                        ("label", models.CharField(max_length=64)),
                        ("secret_digest", models.CharField(max_length=64, unique=True)),
                        ("host_digest", models.CharField(max_length=64)),
                        (
                            "last_used_at",
                            parishkit.stewardship.storage.UTCDateTimeField(
                                blank=True, null=True
                            ),
                        ),
                        (
                            "revoked_at",
                            parishkit.stewardship.storage.UTCDateTimeField(
                                blank=True, null=True
                            ),
                        ),
                        (
                            "end_reason",
                            models.CharField(blank=True, max_length=32, null=True),
                        ),
                    ],
                    options={
                        "db_table": "stewardship_automation_session",
                        "abstract": False,
                        "indexes": [
                            models.Index(
                                fields=["principal_id", "revoked_at"],
                                name="automation_session_principal",
                            )
                        ],
                        "constraints": [
                            models.CheckConstraint(
                                condition=models.Q(version__gte=1),
                                name="stewardship_accounts_automationsession_positive_version",
                            ),
                            models.CheckConstraint(
                                condition=models.Q(scope__in=("read_only", "full")),
                                name="automation_session_scope",
                            ),
                            models.CheckConstraint(
                                condition=models.Q(
                                    secret_digest__regex="^[0-9a-f]{64}$"
                                )
                                & models.Q(host_digest__regex="^[0-9a-f]{64}$"),
                                name="automation_session_digests",
                            ),
                            models.CheckConstraint(
                                condition=models.Q(label__regex="^[^[:cntrl:]]{1,64}$"),
                                name="automation_session_label",
                            ),
                            models.CheckConstraint(
                                condition=models.Q(
                                    expires_at__gt=models.F("authenticated_at")
                                ),
                                name="automation_session_lifetime",
                            ),
                            models.CheckConstraint(
                                condition=models.Q(
                                    revoked_at__isnull=True, end_reason__isnull=True
                                )
                                | models.Q(
                                    revoked_at__isnull=False, end_reason__in=END_REASONS
                                ),
                                name="automation_session_ending",
                            ),
                        ],
                    },
                ),
                migrations.CreateModel(
                    name="AutomationNotice",
                    fields=[
                        *_record_fields(),
                        ("kind", models.CharField(max_length=16)),
                        (
                            "command_type",
                            models.CharField(blank=True, max_length=64, null=True),
                        ),
                        ("campaign_id", models.UUIDField(blank=True, null=True)),
                        (
                            "automation_session",
                            models.ForeignKey(
                                blank=True,
                                null=True,
                                on_delete=django.db.models.deletion.PROTECT,
                                related_name="notices",
                                to="stewardship_accounts.automationsession",
                            ),
                        ),
                    ],
                    options={
                        "db_table": "stewardship_automation_notice",
                        "indexes": [
                            models.Index(
                                fields=["created_at"], name="automation_notice_created"
                            )
                        ],
                        "constraints": [
                            models.CheckConstraint(
                                condition=models.Q(kind__in=NOTICE_KINDS),
                                name="automation_notice_kind",
                            ),
                            models.CheckConstraint(
                                condition=models.Q(command_type__isnull=True)
                                | models.Q(
                                    command_type__regex="^[a-z][a-z0-9_]{0,63}$"
                                ),
                                name="automation_notice_command_type",
                            ),
                        ],
                    },
                ),
                migrations.CreateModel(
                    name="AutomationLogin",
                    fields=[
                        (
                            "portal_session_id",
                            models.UUIDField(primary_key=True, serialize=False),
                        ),
                        (
                            "created_at",
                            parishkit.stewardship.storage.UTCDateTimeField(
                                db_default=django.db.models.functions.datetime.Now(),
                                editable=False,
                            ),
                        ),
                        (
                            "automation_session",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.PROTECT,
                                related_name="logins",
                                to="stewardship_accounts.automationsession",
                            ),
                        ),
                    ],
                    options={"db_table": "stewardship_automation_login"},
                ),
                migrations.CreateModel(
                    name="AutomationNoticeAcknowledgement",
                    fields=[
                        *_record_fields(),
                        ("administrator_id", models.UUIDField()),
                        (
                            "notice",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.PROTECT,
                                related_name="acknowledgements",
                                to="stewardship_accounts.automationnotice",
                            ),
                        ),
                    ],
                    options={
                        "db_table": "stewardship_automation_notice_ack",
                        "constraints": [
                            models.UniqueConstraint(
                                fields=("notice", "administrator_id"),
                                name="automation_notice_ack_identity",
                            )
                        ],
                    },
                ),
            ]
        ),
    ]
