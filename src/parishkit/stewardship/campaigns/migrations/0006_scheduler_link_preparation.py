"""Forward migration: the scheduler may record and discard go-live link work.

Go-live sequencing (#462) moves inactive Family link preparation, and its
discard when ParishSoft data moved on, from the Administrator's clicks into
the scheduler's go-live producer. Until now the
``stewardship_production_tokens_intake_v1`` trigger, shared by both tables,
admitted only the web login acting for a current Administrator. It now also
admits the scheduler login, but only when the actor is the Administrator who
started the go-live (the request's ``initiated_by_id``; for a discard, the
transition of the preparation it discards), while that Administrator is still
current and the request is ``cleanup_complete``. Every other check is
unchanged. The retry check (``stewardship_production_tokens_task_pin_v1``) is
not touched: retries still come only from the web.

Its SQL is the frozen file
``schema/migrations/0025_scheduler_link_preparation.sql``, read whole and
never parsed: ``CREATE OR REPLACE`` of the function with the
fresh-install baseline's text (``schema/activation_tokens.sql``) as it stands
at this release, then a DO block that refuses to commit unless the new body
is installed, still SECURITY DEFINER with the pinned search path. A digest
test pins the frozen file, and a second test checks that this copy equals the
baseline's. Reversing needs its own forward migration, so, like the baseline,
it has no reverse operation.

It depends on its app's latest migration and on the migration that
installed the previous frozen file (0024), so the files apply in prefix
order.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0025_scheduler_link_preparation.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_campaigns", "0005_restore_review"),
        # The migration that installed the previous frozen file (0024).
        ("stewardship_jobs", "0014_task_event_retention"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
