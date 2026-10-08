"""Forward migration: settle restore holds after release; refuse a re-run.

Its SQL is the frozen file ``schema/migrations/0029_held_emails.sql``, read
whole and never parsed. It lets an Administrator decide an undecided held
email after a restore review is released (#757), for the current campaign
only, and refuses ``restore_begin`` while a review is already open for the
same backup (#799). It ends with a DO block that refuses to commit unless
both replaced function bodies are installed. No model changes, so there is no
state operation. Reversing needs its own forward migration, so, like the
baseline, it has no reverse operation.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0029_held_emails.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The migration that installed frozen file 0019 (#741). The agreed
        # campaigns order is 0004 (#741), 0005_scheduler_link_preparation
        # (#802), then this. When those land, this depends on 0005 and on
        # the migration that installs frozen 0028, so the files apply in
        # prefix order (see the PR body's dependency plan).
        ("stewardship_campaigns", "0004_restore_review"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
