"""Forward migration: the Mail message page's per-address outcomes (#806).

Its SQL is the frozen file
``schema/migrations/0068_mail_message_address_outcomes.sql`` (prefix 0068 in
the repository-wide file sequence), read whole and never parsed. It creates
``stewardship_delivery_recipient_outcomes_v1(message, actor)``, a SECURITY
DEFINER read that returns, for an Administrator and to the web login only,
each routed address of an email's newest rendering with what its latest
attempt recorded for it (delivered, refused for now or for good, not sure it
arrived, or not delivered), and ends with a DO block that refuses to commit
unless the function is installed as declared. No model changes, so there is
no state operation. Reversing needs its own forward migration.

It follows the users swap (#952), so its function is granted only once it
is applied. It alters no table.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0068_mail_message_address_outcomes.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The jobs app's previous migration.
        ("stewardship_jobs", "0023_backup_request"),
        # The migration that installs the previous frozen file (0067, #1018),
        # so the files apply in prefix order. It follows the users swap, so
        # `migrate --users-upgrade-base` never applies this file and its
        # function is registered in runtime_grants.CLOSURE_FUNCTIONS.
        ("stewardship_reports", "0018_campaign_comparison"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
