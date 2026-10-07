"""The #556 forward migration's frozen file installs and checks itself.

``0020_download_audit_context.sql`` replaces ``stewardship_safe_context_v1``
so the ``action`` context admits an on-request report's mode, filter, talent
option and snapshot. Its closing DO block must refuse the old allowlist (0008's
copy, the definition every database had before) and a replacement that
changed the function's attributes, and the whole file must run again cleanly
on an installed database. The fields themselves are tested in
``test_audit_context_postgresql.py``.
"""

import re
from pathlib import Path

import pytest
from django.db import DatabaseError, connection, transaction

pytestmark = pytest.mark.django_db(transaction=True)

MIGRATIONS = Path(__file__).resolve().parents[3] / (
    "src/parishkit/stewardship/schema/migrations"
)
TEXT = (MIGRATIONS / "0020_download_audit_context.sql").read_text(encoding="utf-8")
CHECK = re.search(r"^DO \$check\$.*?^\$check\$;", TEXT, re.M | re.S)[0]
ALLOWLIST = re.compile(
    r"^CREATE OR REPLACE FUNCTION public\.stewardship_safe_context_v1\(.*?^END \$_\$;",
    re.M | re.S,
)
# The definition before #556: the last frozen file that replaced it (0008;
# 0009 to 0019 leave it alone).
OLD = ALLOWLIST.search((MIGRATIONS / "0008_log_detail.sql").read_text())[0]


def test_the_whole_file_runs_again_on_an_installed_database():
    """Idempotent: the same body and attributes pass its own check."""
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(TEXT)
        transaction.set_rollback(True)


def test_the_new_body_is_the_old_one_plus_the_report_fields():
    """Nothing but #556's keys and branches changed since 0008's copy."""
    new = ALLOWLIST.search(TEXT)[0]
    assert len(new) - len(OLD) < 1000
    for line in OLD.splitlines():
        if "'file_fingerprint']" in line:
            continue
        assert line in new


def test_the_check_refuses_the_old_allowlist():
    """A database left on the pre-#556 allowlist fails the migration."""
    with (
        connection.cursor() as cursor,
        pytest.raises(DatabaseError, match="#556 report fields"),
        transaction.atomic(),
    ):
        cursor.execute(OLD)
        cursor.execute(CHECK)


def test_the_check_refuses_changed_attributes():
    """A replacement that lost IMMUTABLE fails, even with the right fields."""
    with (
        connection.cursor() as cursor,
        pytest.raises(DatabaseError, match="wrong volatility"),
        transaction.atomic(),
    ):
        new = ALLOWLIST.search(TEXT)[0]
        assert "LANGUAGE plpgsql IMMUTABLE" in new
        cursor.execute(new.replace("LANGUAGE plpgsql IMMUTABLE", "LANGUAGE plpgsql"))
        cursor.execute(CHECK)
