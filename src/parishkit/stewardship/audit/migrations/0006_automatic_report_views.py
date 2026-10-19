"""Forward migration: automatic report views are marked so (#1620).

Its SQL is the frozen file ``schema/migrations/0122_automatic_report_views.sql``
(prefix 0122 in the repository-wide file sequence), read whole and never
parsed. It widens the closed action context (``stewardship_safe_context_v1``)
with the boolean ``automatic`` key, which a Response dashboard or Campaign
comparison view records when the open page re-read itself to bring newer
figures in. It ends with a DO block that refuses to commit unless the new
body is installed as declared. No model changes, so there are no state
operations. Reversing needs its own forward migration, so, like the
baseline, it has no reverse operation.

It lives in the audit app, which owns the context rules, and depends on the
migrations that install the previous frozen files (0120 and 0121), so the
files apply in prefix order. That keeps it in the users swap's closure: the swap test
runs this file's replacement again after the files it reinstalls bring back
an older safe context (``CLOSURE_REPLACED_ONLY``). Its safe context is
0109's body plus ``automatic``, so it keeps every earlier key.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0122_automatic_report_views.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The audit app's previous migration (0117, #1573).
        ("stewardship_audit", "0005_log_search_indexes"),
        # The migration that installs frozen file 0120 (the leader emails).
        ("stewardship_jobs", "0035_leader_emails"),
        # The migration that installs the previous frozen file (0121, the
        # digest skip dates), so the files apply in prefix order.
        ("stewardship_campaigns", "0014_digest_skip_dates"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
