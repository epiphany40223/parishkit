"""Forward migration: a Ministry request's details follow its Edit.

Its SQL is the frozen file ``schema/migrations/0144_edited_ministry_contacts.sql``
(prefix 0144, provisional, assigned in the repository-wide file sequence),
read whole and never parsed (#1718). It replaces
``stewardship_ministry_packet_v1`` and ``stewardship_ministry_report_v3`` so
a request that review's Edit (#1684) accepted with another Ministry carried
forward is listed, with its Member's contact details, under that Ministry,
whose leaders now decide it, and no longer under the Family's. The report's
By Ministry counts count it under both, as the follow-up selection lists
it. It ends with a DO block that refuses to commit unless both selections
are installed as declared with their grants and settings unchanged. No
model changes, so there is no state operation. Reversing needs its own
forward migration, so, like the baseline, it has no reverse operation.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0144_edited_ministry_contacts.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_reports", "0031_leader_request_contacts"),
        # The migration that installs the previous frozen file (0132, the
        # review pipeline's Edit, #1684), so the files apply in prefix order.
        ("stewardship_workflows", "0003_ministry_review_pipeline"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
