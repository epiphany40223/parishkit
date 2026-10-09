"""The #664 forward migration's frozen file installs and checks itself.

``0022_directory_member_search.sql`` replaces the directory selection. Its
closing DO block must refuse the old body (no Member or envelope match) and
a replacement that changed the function's grants, and the whole file must
run again cleanly on an installed database, where it is a no-op. The search
behavior itself is tested in ``test_directories_postgresql.py``.
"""

import re
from pathlib import Path

import pytest
from django.db import DatabaseError, connection, transaction

pytestmark = pytest.mark.django_db(transaction=True)

FROZEN = (
    Path(__file__).resolve().parents[3]
    / "src/parishkit/stewardship/schema/migrations/0022_directory_member_search.sql"
)
TEXT = FROZEN.read_text(encoding="utf-8")
CHECK = re.search(r"^DO \$check\$.*?^\$check\$;", TEXT, re.M | re.S)[0]
ACL = re.search(
    r"^SELECT set_config\('stewardship\.migration_0022_acl'.*?;$", TEXT, re.M | re.S
)[0]
FUNCTION = re.search(
    r"^CREATE OR REPLACE FUNCTION "
    r"public\.stewardship_directory_report_v1\(.*?^END \$\$;",
    TEXT,
    re.M | re.S,
)[0]


def _old_body():
    """The pre-#664 definition: no Member match, no envelope column.

    It rebuilds the old body by removing exactly the lines #664 added (each
    must be present once), which yields main's pre-#664 definition. Upgrade
    parity tests cover the real old-to-new path; this only feeds the check.
    """
    old = re.sub(
        r"\), member_matches AS MATERIALIZED \(.*?(?=\), filtered AS MATERIALIZED \()",
        "",
        FUNCTION,
        flags=re.S,
    )
    for added in (
        "        coalesce(f.value->>'envelopeNumber','') AS envelope_number,\n",
        "          OR position(o.f->>'search' IN envelope_number)>0\n",
        "          OR r.source_key IN (SELECT family_key FROM member_matches)\n",
        "'envelope_number',",
    ):
        assert added in old
        old = old.replace(added, "", 1)
    return old


def test_the_whole_file_runs_again_on_an_installed_database():
    """Idempotent: the same body, attributes and grants pass its own check."""
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(TEXT)
        transaction.set_rollback(True)


def test_the_check_refuses_the_old_selection():
    """A database left on the pre-#664 body fails the migration."""
    with (
        connection.cursor() as cursor,
        pytest.raises(DatabaseError, match="not replaced with the #664 search"),
        transaction.atomic(),
    ):
        cursor.execute(ACL)
        cursor.execute(_old_body())
        cursor.execute(CHECK)


def test_the_check_refuses_changed_grants():
    """A replacement that changed who may execute the selection fails."""
    with (
        connection.cursor() as cursor,
        pytest.raises(DatabaseError, match="lost or changed its grants"),
        transaction.atomic(),
    ):
        cursor.execute("SET LOCAL search_path = public")
        cursor.execute(ACL)
        cursor.execute(
            "REVOKE ALL ON FUNCTION public.stewardship_directory_report_v1"
            "(uuid, jsonb, integer) FROM PUBLIC"
        )
        cursor.execute(CHECK)
