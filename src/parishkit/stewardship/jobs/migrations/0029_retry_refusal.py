"""Forward migration: a follow-up's permanent refusal is a recipient refusal (#806).

Its SQL is the frozen file ``schema/migrations/0079_retry_refusal.sql``
(prefix 0079 in the repository-wide file sequence), read whole and never
parsed. It replaces ``stewardship_recipient_refusal_guard_v1`` with the
baseline's final definition, which also admits a refusal evidenced by the
latest outcome of an accepted attempt's follow-up to its addresses refused
for now (0077), and ends with a DO block that refuses to commit unless the
guard is installed as declared. No model changes, so there is no state
operation. Reversing needs its own forward migration.

It follows the users swap (#952) and creates no table or routine, so it
adds nothing to the swap closure's registries.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0079_retry_refusal.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The jobs app's previous migration, which installs 0078 (the
        # debug-off switch), so the frozen files apply in prefix order. This
        # one's guard reads stewardship_outbox_address_retry, from 0077.
        ("stewardship_jobs", "0028_debug_off_switch"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
