"""Forward migration: setup credential steps accept a sign-in an hour old.

Its SQL is the frozen file ``schema/migrations/0104_setup_sign_in_hour.sql``
(prefix 0104, assigned in the repository-wide file sequence), read whole and
never parsed (#1402). It replaces ``stewardship_setup_secret_guard_v1`` so a
staged setup credential admits the original setup session's Google sign-in
up to 60 minutes old (inclusive) instead of five, matching
``sessions.SETUP_FRESH_SECONDS``; ``stewardship_setup_secret_audit_v1`` so
the staged credential's audit context records the sign-in's age; and
``stewardship_safe_context_v1`` (0102's body) so that context admits the
``sign_in_age_seconds`` key. It ends with a DO block that refuses to commit
unless all three are installed as declared with their grants unchanged.
No model changes, so there is no state operation. Reversing needs its own
forward migration, so, like the baseline, it has no reverse operation.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0104_setup_sign_in_hour.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The accounts app's previous migration, which installs the
        # previous frozen file (0102, the setup load void, #1375), so the
        # files apply in prefix order. It follows the users swap, so
        # `migrate --users-upgrade-base` never applies it.
        ("stewardship_accounts", "0023_setup_load_void"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
