"""Forward migration: the person identity reads (#1867).

Its SQL is the frozen file ``schema/migrations/0157_person_identity_reads.sql``
(prefix 0157, assigned centrally in the repository-wide file sequence), read
whole and never parsed. It adds two Administrator-only definer reads that
return identifiers only: ``stewardship_address_people_v1`` (the ParishSoft
Families and Members whose contacts hold one address) and
``stewardship_family_users_v1`` (the user record ids whose address is a
contact of one Family or its Members). It ends with a DO block that refuses
to commit unless both are installed as declared. It changes no table, so it
has no state operation. Reversing needs its own forward migration.

It follows the users swap (#952), so its definers are granted only once it
is applied (runtime_grants.CLOSURE_FUNCTIONS).
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0157_person_identity_reads.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The accounts app's previous migration (0125, #1763).
        ("stewardship_accounts", "0030_secret_request_session"),
        # The migration that installs the previous frozen file (0156, the
        # report pipeline, #1825), so the files apply in prefix order.
        ("stewardship_reports", "0040_report_pipeline"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
