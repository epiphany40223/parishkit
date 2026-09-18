"""Fresh-install baseline for the unreleased stewardship application.

This intentionally does not upgrade databases carrying the discarded development
history. See the schema guide before using an existing developer database.
"""

from pathlib import Path

from django.db import migrations

DIRECTORY = Path(__file__).resolve().parents[2] / "schema"


def schema_sql():
    """Install current objects directly, then seed before protection triggers."""
    return "\n".join(
        ["SET LOCAL check_function_bodies = false;", "SET LOCAL search_path = public;"]
        + [
            (DIRECTORY / f"{name}.sql").read_text(encoding="utf-8")
            for name in (
                "functions",
                "tables",
                "seed",
                "guards",
                "delivery",
                "family_dispatch",
                "production",
                "cleanup",
                "daily_digests",
                "daily_dispatch",
                "weekly_digests",
                "weekly_manual",
                "weekly_capture",
                "weekly_fanout",
                "weekly_completion",
                "weekly_dispatch",
                "schedule_reconciliation",
                "exports",
                "delivery_resolution",
                "submission_receipts",
                "report_facts",
                "exact_exports",
                "fact_verification",
                "operational_incidents",
                "operational_fanout",
                "operational_render",
                "operational_dispatch",
            )
        ]
    )


class Migration(migrations.Migration):
    initial = True
    dependencies = [("sessions", "0001_initial")]
    # Irreversible deliberately: development databases are recreated, never
    # downgraded through nonexistent intermediate schema versions.
    operations = [migrations.RunSQL(schema_sql())]
