"""Forward migration: Ministry leaders see a requesting Member's own contact.

Its SQL is the frozen file ``schema/migrations/0117_leader_request_contacts.sql``
(prefix 0117, assigned in the repository-wide file sequence), read whole and
never parsed (#1591). It replaces ``stewardship_ministry_packet_v1`` and
``stewardship_ministry_report_v3`` so a Member's standing request to a
Ministry (its latest version, not withdrawn) is their consent to be
contacted about it: that Ministry's leaders see the Member's own email and
phones even when ParishSoft does not publish them (Administrator decision,
2026-10-17), except on a leave because the Member cannot participate in
any ministries, which keeps the publish flags (2026-10-18). It ends with a
DO block that refuses to commit unless both
selections are installed as declared with their grants and settings
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
    / "0117_leader_request_contacts.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_reports", "0030_financial_local_days"),
        # The migration that installs the previous frozen file on main
        # (0113), so the files apply in prefix order. The numbers between
        # are taken by open pull requests; whichever merges later moves its
        # dependency to the newest frozen file's migration.
        ("stewardship_responses", "0011_census_handling_registry"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
