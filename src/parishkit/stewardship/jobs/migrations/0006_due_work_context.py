"""Forward migration: ``due_work_lag`` records what was late (#634).

Before #634 the due-work checkpoint trigger logged ``due_work_lag`` with an
empty context, so System logs could not say which work was late, by how
much, or which Family send had stalled. The operational context allowlist
(``stewardship_safe_context_v1``) gains a closed ``due_work`` schema, mirrored
by ``audit.schemas.ContextKind.DUE_WORK``, and the trigger copies the
scheduler scan's context into the CRITICAL entry. The scan passes it in the
transaction-local setting ``parishkit.due_work_context``; a context the
allowlist refuses is dropped rather than blocking the failure record.

Its SQL is the frozen file ``schema/migrations/0006_due_work_context.sql``
(the next prefix in the repository-wide file sequence after campaigns'
``0005_occurrence_prepare_ahead.sql``, hence the dependency on that
migration), read whole and never parsed: ``CREATE OR REPLACE`` of both
functions with the fresh-install baseline's text as it stands at this
release, then a DO block that refuses to commit unless both new bodies are
installed. No table, column or constraint changes, so there is no model
state to record. Reversing needs its own forward migration restoring the
old bodies, so, like the baseline, it has no reverse operation.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0006_due_work_context.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_jobs", "0005_automation_incident_kinds"),
        # The migration that installed the previous frozen file, so the
        # files apply in prefix order.
        ("stewardship_campaigns", "0003_occurrence_prepare_ahead"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
