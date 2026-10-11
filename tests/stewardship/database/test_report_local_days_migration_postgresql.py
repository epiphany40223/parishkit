"""The #558 report-days migration's frozen file is its twins plus one change.

``0064_report_local_days.sql`` adds browser-zone versions of the report
selections and export captures, each copied whole from its latest definition
(the 0045 copies of the report and Ministry capture functions, 0040's actor
wrapper and the baseline's information capture) with only #558's change. The
first test checks that against those files: every line of the twin is in the
copy except the lines #558 replaces. Its closing DO block must refuse the
database as it was before (none of the new functions, the triggers on v1)
and a new function whose attributes drifted from its twin's. Each case runs
in a rolled-back transaction.
"""

import re
from pathlib import Path

import pytest
from django.db import DatabaseError, connection, transaction

pytestmark = pytest.mark.django_db(transaction=True)

SCHEMA = Path(__file__).resolve().parents[3] / "src/parishkit/stewardship/schema"
TEXT = (SCHEMA / "migrations/0064_report_local_days.sql").read_text(encoding="utf-8")
CHECK = re.search(r"^DO \$check\$.*?^\$check\$;", TEXT, re.M | re.S)[0]


def body(text, name):
    """One function's whole definition in ``text``."""
    return re.search(
        r"^CREATE (?:OR REPLACE )?FUNCTION (?:public\.)?"
        + re.escape(name)
        + r"\(.*?^(?:END )?\$\$;",
        text,
        re.M | re.S,
    )[0]


def source(name):
    """A schema file's text."""
    return (SCHEMA / name).read_text(encoding="utf-8")


# (twin file, twin, copy, the twin's lines #558 replaces)
COPIES = (
    (
        "migrations/0045_report_duid_sorts.sql",
        "stewardship_information_report_v1",
        "stewardship_information_report_v2",
        ("FUNCTION", "ARRAY['search'", "submitted_at AT TIME ZONE"),
    ),
    (
        "migrations/0045_report_duid_sorts.sql",
        "stewardship_ministry_report_v1",
        "stewardship_ministry_report_v3",
        ("FUNCTION", "submitted_at AT TIME ZONE", "nullif((filters->>"),
    ),
    (
        "migrations/0040_ministry_report_actor_scope.sql",
        "stewardship_ministry_report_v2",
        "stewardship_ministry_report_v4",
        ("FUNCTION", "stewardship_ministry_report_v1("),
    ),
    (
        "information_reports.sql",
        "stewardship_information_export_capture_v1",
        "stewardship_information_export_capture_v2",
        ("FUNCTION", "NEW.document:=stewardship_information_report_v1"),
    ),
    (
        "migrations/0045_report_duid_sorts.sql",
        "stewardship_ministry_export_capture_v1",
        "stewardship_ministry_export_capture_v2",
        (
            "FUNCTION",
            "filters-ARRAY['search'",
            "IF filters-'history'<>",
            "NEW.document:=stewardship_ministry_report_v1",
            "ministry_id,request_action);",
        ),
    ),
)


@pytest.mark.parametrize("twin_file,twin,copy,replaced", COPIES)
def test_each_copy_is_its_latest_twin_plus_only_the_change(
    twin_file, twin, copy, replaced
):
    """Every unchanged line of the latest twin is in the copy, in order."""
    old = body(source(twin_file), twin).splitlines()
    new = body(TEXT, copy).splitlines()
    position = 0
    for line in old:
        if any(marker in line for marker in replaced):
            continue
        assert line in new[position:], (copy, line)
        position = new.index(line, position) + 1
    # The change itself stays small.
    assert len(new) - len(old) < 20, copy


def refused(change, message):
    """``change`` (SQL) then the check, refused with ``message``; rolled back."""
    with (
        connection.cursor() as cursor,
        pytest.raises(DatabaseError, match=message),
        transaction.atomic(),
    ):
        cursor.execute(change)
        cursor.execute(CHECK)


def test_the_check_passes_on_an_installed_database():
    """Everything the file installs is there as declared."""
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(CHECK)
        transaction.set_rollback(True)


def test_the_check_refuses_the_database_before_it():
    """With the triggers back on v1 and the new functions gone (every
    database before 0064), the file cannot commit."""
    refused(
        """
        CREATE OR REPLACE TRIGGER information_export_capture
            BEFORE INSERT OR UPDATE OR DELETE
            ON public.stewardship_information_export_snapshot FOR EACH ROW
            EXECUTE FUNCTION public.stewardship_information_export_capture_v1();
        CREATE OR REPLACE TRIGGER ministry_export_capture
            BEFORE INSERT OR UPDATE OR DELETE
            ON public.stewardship_ministry_export_snapshot FOR EACH ROW
            EXECUTE FUNCTION public.stewardship_ministry_export_capture_v1();
        DROP FUNCTION public.stewardship_information_export_capture_v2();
        DROP FUNCTION public.stewardship_ministry_export_capture_v2();
        DROP FUNCTION public.stewardship_ministry_report_v4(
            uuid,jsonb,uuid,integer,text,integer,integer);
        DROP FUNCTION public.stewardship_ministry_report_v3(
            uuid,jsonb,boolean,bigint[],integer,text,integer,integer);
        DROP FUNCTION public.stewardship_information_report_v2(
            uuid,jsonb,integer,uuid,integer);
        """,
        "does not exist",
    )


@pytest.mark.parametrize(
    "change",
    [
        "ALTER FUNCTION public.stewardship_ministry_report_v3("
        "uuid,jsonb,boolean,bigint[],integer,text,integer,integer) SECURITY DEFINER",
        "ALTER FUNCTION public.stewardship_information_report_v2("
        "uuid,jsonb,integer,uuid,integer) RESET jit",
        "REVOKE EXECUTE ON FUNCTION public.stewardship_ministry_report_v4("
        "uuid,jsonb,uuid,integer,text,integer,integer) FROM PUBLIC",
    ],
)
def test_the_check_refuses_a_copy_that_drifted_from_its_twin(change):
    """Definer's rights, a lost setting or changed grants all fail."""
    refused(change, "did not install")


def test_the_check_refuses_triggers_left_on_v1():
    """Both capture triggers must call the new captures."""
    refused(
        "CREATE OR REPLACE TRIGGER ministry_export_capture "
        "BEFORE INSERT OR UPDATE OR DELETE "
        "ON public.stewardship_ministry_export_snapshot FOR EACH ROW "
        "EXECUTE FUNCTION public.stewardship_ministry_export_capture_v1()",
        "did not repoint",
    )
