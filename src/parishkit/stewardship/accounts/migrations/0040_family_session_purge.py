"""Forward migration: the maintenance task's Family and anonymous session purge (#2362).

Its SQL is the frozen file ``schema/migrations/0199_family_session_purge.sql``
(prefix 0199 in the repository-wide file sequence), read whole and never
parsed. It creates two general-worker definers:
``stewardship_family_session_purge_v1(uuid[])``, which cancels the open
forms of named ended Family sessions, releases their source pins and deletes
the session rows with their Django sessions, and
``stewardship_anonymous_session_purge_v1(integer)``, which deletes expired
Django sessions that no Admin or Family session names. Neither returns a
session key. It ends with a DO block that refuses to commit unless both are
installed as declared. No model state changes. Reversing needs its own
forward migration.

It follows the users swap (#952), so its granted definers are granted only
once it is applied (runtime_grants.CLOSURE_FUNCTIONS).
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0199_family_session_purge.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The accounts app's previous migration, so the app keeps one leaf.
        ("stewardship_accounts", "0039_setup_backup_step"),
        # The migration that installs the previous frozen file (0193).
        ("stewardship_jobs", "0041_backup_uploader_preparation"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
