"""Forward migration: setup credential steps accept a sign-in an hour old.

Its SQL is the frozen file ``schema/migrations/0097_setup_sign_in_hour.sql``
(prefix 0097, assigned in the repository-wide file sequence), read whole and
never parsed (#1402). It replaces ``stewardship_setup_secret_guard_v1`` so a
staged setup credential admits the original setup session's Google sign-in
up to 60 minutes old instead of five, matching
``sessions.SETUP_FRESH_SECONDS``. It ends with a DO block that refuses to
commit unless the guard is installed as declared with its grants unchanged.
No model changes, so there is no state operation. Reversing needs its own
forward migration, so, like the baseline, it has no reverse operation.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0097_setup_sign_in_hour.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_accounts", "0022_drop_ministry_activity"),
        # The migration that installs the previous frozen file (0096, the
        # proposal review, in the responses app), so the files apply in
        # prefix order. It follows the users swap, so
        # `migrate --users-upgrade-base` never applies it.
        ("stewardship_responses", "0006_proposal_review"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
