"""Forward migration: response list audits record their sort order (#851).

Its SQL is the frozen file ``schema/migrations/0039_response_list_sort_audit.sql``
(prefix 0039, assigned in the repository-wide file sequence), read whole and
never parsed. It replaces ``stewardship_safe_context_v1`` with 0038's body
plus one closed ``action`` key, ``report_sort``: a response list's sort token
(``audit.schemas.REPORT_SORTS``). It ends with a DO block that refuses to
commit unless the allowlist is installed with that key and its attributes.
No model changes, so there is no state operation. Reversing needs its own
forward migration, so, like the baseline, it has no reverse operation.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0039_response_list_sort_audit.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The reports app's previous migration.
        ("stewardship_reports", "0006_family_test_names"),
        # The migration that installed the previous frozen file (0038,
        # #617), so the files apply in prefix order.
        ("stewardship_jobs", "0019_admin_command_failure_log"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
