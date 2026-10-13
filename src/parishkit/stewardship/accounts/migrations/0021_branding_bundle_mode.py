"""Forward migration: remember each logo bundle's Basic or Advanced mode (#1062).

Its SQL is the frozen file ``schema/migrations/0082_branding_bundle_mode.sql``,
read whole and never parsed. It adds the nullable ``mode`` column to
``stewardship_branding_bundle`` ("basic" when one image was resized into all
four sizes, "advanced" when the sizes were uploaded separately, NULL for
campaign artwork and every earlier bundle) with its check, and ends with a DO
block that refuses to commit unless both are installed as described.

It depends on its own app's latest migration and on the one that installs
the previous frozen file (0081), so the files apply in prefix order. It follows
the users swap, so `migrate --users-upgrade-base` never applies this file. It
creates no table and no routine, and the web login's table-level grants on
bundles already cover the new column, so nothing is registered in
``runtime_grants``. The state operations record the column and its check in
Django's model state, so ``makemigrations --check`` stays clean. Reversing
needs its own forward migration, so, like the baseline, it has no reverse
operation.
"""

from pathlib import Path

from django.db import migrations, models

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0082_branding_bundle_mode.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_accounts", "0020_campaign_series"),
        # The migration that installed the previous frozen file (0081), so
        # the files apply in prefix order.
        ("stewardship_jobs", "0030_bounce_report"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))
            ],
            state_operations=[
                migrations.AddField(
                    model_name="brandingbundle",
                    name="mode",
                    field=models.CharField(max_length=8, null=True),
                ),
                migrations.AddConstraint(
                    model_name="brandingbundle",
                    constraint=models.CheckConstraint(
                        condition=models.Q(("mode__isnull", True))
                        | models.Q(("mode__in", ["basic", "advanced"])),
                        name="branding_bundle_mode",
                    ),
                ),
            ],
        )
    ]
