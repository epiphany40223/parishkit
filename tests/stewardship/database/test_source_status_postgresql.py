"""No installed selection names a Member or Family in the retired words (#1216).

The static guard in ``test_source_status.py`` reads the baseline files, but a
function a frozen migration created exists only there; this reads every
installed function body in the fresh schema instead, so a selection that
still prints "Unavailable Member" or "Unavailable Family" cannot hide in a
migration-owned function.
"""

import pytest
from django.db import connection

from ..test_source_status import RETIRED


@pytest.mark.django_db(transaction=True)
def test_no_installed_function_uses_a_retired_phrase():
    """Every public function body uses the shared words (#1216)."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT p.proname, phrase FROM pg_proc p "
            "JOIN pg_namespace n ON n.oid=p.pronamespace "
            "CROSS JOIN unnest(%s::text[]) phrase "
            "WHERE n.nspname='public' AND strpos(p.prosrc, phrase)>0 "
            "ORDER BY 1, 2",
            [list(RETIRED)],
        )
        assert cursor.fetchall() == []
