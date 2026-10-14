"""Forward migration: the publish worker's proposal states (#528, BG-09).

Its SQL is the frozen file ``schema/migrations/0090_publication_worker.sql``
(prefix 0090, assigned in the repository-wide file sequence), read whole and
never parsed. It replaces ``stewardship_response_derived_guard_v1`` with its
current body plus the worker's publication branch (queued, published, and
back to pending or failed), and two of 0087's guards with the #1243 review
fixes. No table, column or grant changes, so there is no state operation.
Reversing needs its own forward migration, so, like the baseline, it has no
reverse operation.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0090_publication_worker.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_responses", "0004_publication_plans"),
        # The migration that installs the previous frozen file (0089,
        # retry refusal log), so the files apply in prefix order. It follows
        # the users swap, so `migrate --users-upgrade-base` never applies it.
        ("stewardship_audit", "0003_retry_refusal_log"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
