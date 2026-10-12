"""Forward migration: remembered reminder series (#469, slice 2b).

Its SQL is the frozen file ``schema/migrations/0075_campaign_series.sql``,
read whole and never parsed. It admits the ``campaign-series-v11`` document
schema (every rule of ``users-v10`` plus an optional ``schedule_series``
section), adds it beside ``users-v10`` in the campaign, content, Ministry and
policy projection and completeness functions, and teaches the users-form
activation guard that it is the users form and that a configuration with
the section never activates another schema. It ends with a DO block that
refuses to commit unless all of it is installed as declared.

The state operation records the same constraint change for Django. Nothing
existing changes; reversing needs its own forward migration, so, like the
baseline, it has no reverse operation.
"""

from pathlib import Path

from django.db import migrations, models

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0075_campaign_series.sql"
)

VALIDATION_SCHEMAS = [
    "parish-integrations-v1",
    "foundation-policy-v2",
    "campaign-foundation-v3",
    "bootstrap-policy-v1",
    "ministry-activity-v4",
    "campaign-content-v5",
    "source-cadence-v8",
    "users-v10",
    "bootstrap-users-v10",
    "campaign-series-v11",
]


class Migration(migrations.Migration):
    dependencies = [
        # Its own app's latest migration, which also installs the previous
        # frozen file (0074, #1062), so the files apply in prefix order. It
        # follows the users swap, so `migrate --users-upgrade-base` never
        # applies this file; it creates no routine, table or column.
        ("stewardship_accounts", "0019_wide_menu_logo"),
    ]

    operations = [
        migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8")),
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveConstraint(
                    model_name="appliedconfigurationversion",
                    name="configuration_validation_schema",
                ),
                migrations.AddConstraint(
                    model_name="appliedconfigurationversion",
                    constraint=models.CheckConstraint(
                        condition=models.Q(
                            ("validation_schema__in", VALIDATION_SCHEMAS)
                        ),
                        name="configuration_validation_schema",
                    ),
                ),
            ]
        ),
    ]
