"""Forward migration: a Campaign setup step kept as is (#1404).

Its SQL is the frozen file ``schema/migrations/0105_campaign_setup_review.sql``
(prefix 0105, assigned in the repository-wide file sequence), read whole and
never parsed. It creates ``stewardship_campaign_setup_review`` (an
Administrator's Continue on an unchanged Campaign setup step), its insert
guard (a current Administrator, the current campaign while it is a Testing
draft with no go-live started) and its deferred audit check. The state
operation records the same table in Django's model state, so
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
    / "0105_campaign_setup_review.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The accounts app's previous migration, which installs the previous
        # frozen file (#1406's setup sign-in window), so the files
        # apply in prefix order. It follows the users swap, so
        # `migrate --users-upgrade-base` never applies this file and its
        # table is registered in runtime_grants.CLOSURE_TABLES.
        ("stewardship_accounts", "0024_setup_sign_in_hour"),
        ("stewardship_campaigns", "0001_initial"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))
            ],
            state_operations=[
                migrations.CreateModel(
                    name="CampaignSetupReview",
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
                        ("step", models.CharField(max_length=16)),
                        (
                            "item",
                            models.CharField(blank=True, default="", max_length=64),
                        ),
                        (
                            "campaign",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.PROTECT,
                                related_name="+",
                                to="stewardship_campaigns.campaign",
                            ),
                        ),
                    ],
                    options={
                        "db_table": "stewardship_campaign_setup_review",
                        "constraints": [
                            models.UniqueConstraint(
                                fields=("campaign", "step", "item"),
                                name="campaign_setup_review_once",
                            ),
                            models.CheckConstraint(
                                condition=models.Q(actor_id__isnull=False),
                                name="campaign_setup_review_actor",
                            ),
                            models.CheckConstraint(
                                condition=models.Q(
                                    step__in=(
                                        "settings",
                                        "share_options",
                                        "content",
                                        "schedules",
                                        "images",
                                    )
                                ),
                                name="campaign_setup_review_step",
                            ),
                            models.CheckConstraint(
                                condition=models.Q(item="")
                                | models.Q(
                                    step="content",
                                    item__regex="^(page|email):[a-z][a-z0-9_]{0,47}$",
                                ),
                                name="campaign_setup_review_item",
                            ),
                        ],
                    },
                ),
            ],
        ),
    ]
