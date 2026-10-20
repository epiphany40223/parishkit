"""Forward migration: link a Member the Family added to their ParishSoft record.

Its SQL is the frozen file ``schema/migrations/0141_added_member_links.sql``
(number 0141, provisional until it is assigned in merge order), read whole
and never parsed (#1670). It creates ``stewardship_added_member_link`` (one
immutable row per Family campaign and added Member, which only the promoting
source worker writes, with the snapshot's own evidence), its guard and the
SECURITY DEFINER ``stewardship_added_member_candidates_v1`` that finds the
links the evidence supports, and replaces ``stewardship_ministry_packet_v1``
and ``stewardship_ministry_report_v3`` so the cannot-participate contact
rule reads an added Member and their linked ParishSoft Member as one
person. It ends with a DO block that refuses to commit unless all of it is
installed. The state operation records the model in Django's model state,
so ``makemigrations --check`` stays clean. Reversing needs its own forward
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
    / "0141_added_member_links.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # This app's previous migration, so the app keeps one leaf.
        ("stewardship_responses", "0020_new_member_id_correction"),
        # The migration that installs the previous frozen file (0140, a
        # Ministry request's details follow its Edit, #1718), whose
        # selection bodies this file copies, so the files apply in prefix
        # order. It follows the users swap, so the new table is in
        # runtime_grants.CLOSURE_TABLES.
        ("stewardship_reports", "0035_edited_ministry_contacts"),
        ("stewardship_campaigns", "0014_digest_skip_dates"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))
            ],
            state_operations=[
                migrations.CreateModel(
                    name="AddedMemberLink",
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
                        ("proposed_key", models.CharField(max_length=36)),
                        ("member_key", models.CharField(max_length=20)),
                        ("snapshot_id", models.UUIDField()),
                        (
                            "family",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.PROTECT,
                                related_name="+",
                                to="stewardship_campaigns.familycampaign",
                            ),
                        ),
                        (
                            "proposal",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.PROTECT,
                                related_name="+",
                                to="stewardship_responses.proposedchange",
                            ),
                        ),
                    ],
                    options={
                        "db_table": "stewardship_added_member_link",
                        "constraints": [
                            models.UniqueConstraint(
                                fields=("family", "proposed_key"),
                                name="added_member_link_proposed_once",
                            ),
                            models.UniqueConstraint(
                                fields=("family", "member_key"),
                                name="added_member_link_member_once",
                            ),
                            models.CheckConstraint(
                                condition=models.Q(("actor_id__isnull", True)),
                                name="added_member_link_identity",
                            ),
                        ],
                    },
                ),
            ],
        ),
    ]
