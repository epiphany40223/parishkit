"""Forward migration: the corpus evidence checks start from the snapshot (#629).

Its SQL is the frozen file
``schema/migrations/0044_corpus_evidence_from_snapshot.sql``, read whole and
never parsed. It replaces ``stewardship_source_corpus_evidence`` so that each
relationship and contact/address owner check is skipped when its collection
has no rows in the snapshot, and otherwise starts from that snapshot's rows
instead of every retained payload. The refusals are unchanged. It ends with a
DO block that refuses to commit unless the new body is installed with the
baseline's attributes. No model changes, so there is no state operation.
Reversing needs its own forward migration, so, like the baseline, it has no
reverse operation.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0044_corpus_evidence_from_snapshot.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_source", "0006_slot_decisions"),
        # The migration that installed the previous frozen file (0043,
        # #982), so the files apply in prefix order.
        ("stewardship_jobs", "0021_alert_local_time"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
