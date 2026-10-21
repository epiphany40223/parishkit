"""Forward migration: the Response dashboard's export carries its Summary (#1557).

Its SQL is the frozen file
``schema/migrations/0142_participation_export_summary.sql`` (prefix 0142,
provisional until it is assigned in the repository-wide file sequence), read
whole and never parsed. It creates the immutable
``stewardship_participation_export_snapshot``, which holds the page's
Summary (the statistics and the funnel counts) as read when a participation
export is requested, with its capture guard; adds a deferred check that a
participation request commits only with its capture (a latest-data handoff
excepted); and replaces 0109's discard so an ended export's capture is
cleared too. It ends with a DO block that refuses to commit unless all of
that is installed. The state operation records the same model, so
``makemigrations --check`` stays clean. Reversing needs its own forward
migration, so it has no reverse operation.

It depends on the reports app's previous migration and on the migration
that installs the previous frozen file (0141), so the files apply in prefix
order. That puts it in the users swap's closure: its table is in
``runtime_grants.CLOSURE_TABLES``, its two new functions are
``CLOSURE_CREATED`` in the users swap test, and that test runs its discard
again after 0109's file brings back the older one.
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
    / "0142_participation_export_summary.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_campaigns", "0014_digest_skip_dates"),
        # The reports app's previous migration (0140, #1718).
        ("stewardship_reports", "0035_edited_ministry_contacts"),
        # The automatic report views (0122, #1620), whose discard it widens.
        ("stewardship_audit", "0006_automatic_report_views"),
        # The migration that installs the previous frozen file (0141, #1670),
        # so the files apply in prefix order.
        ("stewardship_responses", "0021_added_member_links"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))
            ],
            state_operations=[
                migrations.CreateModel(
                    name="ParticipationExportSnapshot",
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
                        ("actor_id", models.UUIDField(editable=False)),
                        ("correlation_id", models.UUIDField(editable=False)),
                        ("document", models.JSONField()),
                        (
                            "campaign",
                            models.ForeignKey(
                                db_index=False,
                                on_delete=django.db.models.deletion.PROTECT,
                                to="stewardship_campaigns.campaign",
                            ),
                        ),
                        (
                            "request",
                            models.OneToOneField(
                                on_delete=django.db.models.deletion.PROTECT,
                                related_name="summary_snapshot",
                                to="stewardship_reports.exportrequest",
                            ),
                        ),
                    ],
                    options={
                        "db_table": "stewardship_participation_export_snapshot",
                        "indexes": [
                            models.Index(
                                fields=["correlation_id"],
                                name="participation_export_correl",
                            ),
                            models.Index(
                                fields=["campaign"],
                                name="participation_export_campaign",
                            ),
                        ],
                    },
                ),
            ],
        ),
    ]
