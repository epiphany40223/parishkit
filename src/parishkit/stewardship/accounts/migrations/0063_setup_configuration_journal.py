"""Retain original-attempt bindings and narrowly scoped setup abort decisions."""

import uuid
from importlib import import_module

from django.db import migrations, models
from django.db.models.functions import Now

from parishkit.stewardship.observability import current_correlation
from parishkit.stewardship.storage import UTCDateTimeField


def _fields():
    """Freeze this migration's ordinary immutable-record columns independently."""
    return [
        (
            "id",
            models.UUIDField(
                default=uuid.uuid4, editable=False, primary_key=True, serialize=False
            ),
        ),
        ("created_at", UTCDateTimeField(db_default=Now(), editable=False)),
        ("actor_id", models.UUIDField(blank=True, editable=False, null=True)),
        (
            "correlation_id",
            models.UUIDField(
                db_index=True, default=current_correlation, editable=False
            ),
        ),
    ]


_previous = (
    import_module(
        "parishkit.stewardship.accounts.migrations.0057_integration_selection_schema"
    )
    .Migration.operations[1]
    .constraint.condition.children[0][1]
)


class Migration(migrations.Migration):
    dependencies = [("stewardship_accounts", "0062_setup_delete_guard_order")]
    operations = [
        migrations.RemoveConstraint(
            "configurationchangerequest", "config_request_schema"
        ),
        migrations.AddConstraint(
            "configurationchangerequest",
            models.CheckConstraint(
                condition=models.Q(
                    request_schema__in=[*_previous, "initial-setup-patch-v7"]
                ),
                name="config_request_schema",
            ),
        ),
        migrations.CreateModel(
            name="SetupConfigurationIntent",
            fields=_fields()
            + [
                ("attempt_version", models.PositiveBigIntegerField()),
                (
                    "attempt",
                    models.OneToOneField(
                        on_delete=models.PROTECT, to="stewardship_accounts.setupattempt"
                    ),
                ),
                (
                    "request",
                    models.OneToOneField(
                        on_delete=models.PROTECT,
                        to="stewardship_accounts.configurationchangerequest",
                    ),
                ),
            ],
            options={
                "db_table": "stewardship_setup_config_intent",
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(attempt_version__gte=1),
                        name="setup_intent_positive_version",
                    )
                ],
            },
        ),
        migrations.CreateModel(
            name="SetupConfigurationAbort",
            fields=_fields()
            + [
                (
                    "intent",
                    models.OneToOneField(
                        on_delete=models.PROTECT,
                        to="stewardship_accounts.setupconfigurationintent",
                    ),
                ),
                ("reason", models.CharField(max_length=16)),
            ],
            options={
                "db_table": "stewardship_setup_config_abort",
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(
                            reason__in=[
                                "cancelled",
                                "session",
                                "idle",
                                "watchdog",
                                "absolute",
                            ]
                        ),
                        name="setup_abort_reason",
                    )
                ],
            },
        ),
        migrations.RunSQL(
            migrations.RunSQL.noop,
            """
            LOCK TABLE public.stewardship_config_request,
                public.stewardship_setup_config_intent,
                public.stewardship_setup_config_abort IN ACCESS EXCLUSIVE MODE;
            DO $$ BEGIN
                IF EXISTS (SELECT 1 FROM public.stewardship_config_request
                    WHERE request_schema='initial-setup-patch-v7')
                    OR EXISTS (SELECT 1 FROM public.stewardship_setup_config_intent)
                    OR EXISTS (SELECT 1 FROM public.stewardship_setup_config_abort) THEN
                    RAISE EXCEPTION 'Setup finalization history prevents downgrade'
                        USING ERRCODE='23514';
                END IF;
            END $$;
            """,
        ),
    ]
