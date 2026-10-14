"""Forward migration: the campaign comparison reads a legacy time zone alias.

Its SQL is the frozen file ``schema/migrations/0095_canonical_timezone.sql``
(prefix 0095, assigned in the repository-wide file sequence), read whole and
never parsed (#1286). It replaces ``stewardship_campaign_comparison_v1`` so
the day's end applies the stored configuration zone through
``stewardship_timezone_name_v1``, as every other stored-zone conversion
does: PostgreSQL's image lacks the IANA backward links (US/Eastern, ...)
the accepted-name catalog admits. It ends with a DO block that refuses to
commit unless the function is installed as declared with its grants
unchanged. No model changes, so there is no state operation. Reversing
needs its own forward migration, so, like the baseline, it has no reverse
operation.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0095_canonical_timezone.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The reports app's previous migration, which installs 0094 (#1216),
        # the previous frozen file, so the files apply in prefix order.
        ("stewardship_reports", "0026_source_status_names"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
