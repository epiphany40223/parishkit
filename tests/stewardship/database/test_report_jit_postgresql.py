"""Report functions turn JIT off (#277, #322).

JIT is on at the database level, and compiling these large report queries cost
about 2.3 s per call at parish size while saving nothing.
"""

import pytest
from django.db import connection

pytestmark = pytest.mark.django_db(transaction=True)

REPORTS = (
    "stewardship_directory_report_v1",
    "stewardship_financial_report_v1",
    "stewardship_information_report_v1",
    "stewardship_talent_report_v1",
    "stewardship_ministry_report_v1",
)


def test_every_report_function_turns_jit_off():
    """Each report function pins jit=off beside its trusted search path."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT p.proname,p.proconfig FROM pg_proc p "
            "JOIN pg_namespace n ON n.oid=p.pronamespace "
            "WHERE n.nspname='public' AND p.proname=ANY(%s)",
            [list(REPORTS)],
        )
        rows = dict(cursor.fetchall())
    assert rows.keys() == set(REPORTS)
    for name, options in rows.items():
        assert "jit=off" in options, name
        assert "search_path=pg_catalog, public, pg_temp" in options, name
