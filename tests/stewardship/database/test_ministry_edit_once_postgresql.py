"""An edited Ministry request belongs to one Ministry (#2331, frozen 0198).

The Administrator decided (2026-11-01) that a request review's Edit carried
into another Ministry is counted and listed only under that Ministry, its
Ministry in force. These tests drive a real Edit through the worklist's
service and read By Ministry (the Ministry report selection) and the
worklist (the follow-up selection) through the restricted web login, and
check 0198's closing check on the installed schema and against the real
earlier bodies of its frozen predecessors (0131 and 0141). Each closing
check statement runs in a transaction that is rolled back.
"""

import re

import pytest
from django.db import DatabaseError, connection, transaction

from .test_hide_gone_members_postgresql import SCHEMA_FILES, definition
from .test_ministry_reports_postgresql import actor, page, setup
from .test_ministry_review_postgresql import decide, view, withdraw_leave

pytestmark = pytest.mark.django_db(transaction=True)

MIGRATION = (SCHEMA_FILES / "migrations/0198_ministry_edit_once.sql").read_text(
    encoding="utf-8"
)
# The newest earlier definition of each replaced selection.
EARLIER = {
    "stewardship_ministry_followup_v1": "0131_ministry_review_pipeline.sql",
    "stewardship_ministry_packet_v1": "0141_added_member_links.sql",
    "stewardship_ministry_report_v3": "0141_added_member_links.sql",
}
CHECK = MIGRATION[MIGRATION.index("DO $check$") :]
ACL = MIGRATION[
    MIGRATION.index("SELECT set_config('stewardship.migration_0198_acl'") : (
        MIGRATION.index(", true);", MIGRATION.index("migration_0198_acl"))
        + len(", true);")
    )
]
REPLACED = re.findall(r"^CREATE OR REPLACE FUNCTION public\.(\w+)\(", MIGRATION, re.M)
REFUSED = "\\(edited Ministry requests once\\) did not replace"


def earlier(name):
    """The real definition 0198 replaced, from its frozen file."""
    text = (SCHEMA_FILES / "migrations" / EARLIER[name]).read_text(encoding="utf-8")
    return definition(text, name)


def check_refuses(statement, message=REFUSED):
    """Run ``statement`` and then 0198's closing check, which must refuse it."""
    with (
        pytest.raises(DatabaseError, match=f"Migration 0198 {message}"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute("SET LOCAL check_function_bodies = false")
        cursor.execute(ACL)
        cursor.execute(statement)
        cursor.execute(CHECK)


def summaries(harness, **reader):
    """By Ministry's join counts for ``reader``, by Ministry DUID."""
    return {row["duid"]: row["joining"] for row in page(harness, **reader)["summaries"]}


@pytest.mark.usefixtures("earlier_rule")
def test_an_edited_request_is_counted_and_listed_once(response_service):
    """Staff edit Member 3's Food pantry (9) join into Ministry 4: By
    Ministry counts it under Ministry 4 alone, the worklist's Ministry
    filter finds it only there, and Food pantry's leader no longer sees it,
    while Ministry 4's leader does."""
    harness = setup(response_service)
    staff = actor(harness, {"staff"}, ())
    fours = actor(harness, {"ministry_leader"}, (4,))
    pantry = actor(harness, {"ministry_leader"}, (9,))
    join = withdraw_leave(harness)
    # Before the Edit it is Food pantry's, as every unedited request is.
    assert summaries(harness) == {4: 0, 9: 1}
    assert str(join.pk) in view(harness, staff, ministry="9")
    assert str(join.pk) not in view(harness, staff, ministry="4")
    assert str(join.pk) in view(harness, pantry)
    assert str(join.pk) not in view(harness, fours)

    decide(harness, staff, "edit", join, fields={"value": ["4"]})
    assert join.reviewed_ministry_duid == 4
    # Counted once, under the Ministry in force, so the column sums to the
    # one request.
    assert summaries(harness) == {4: 1, 9: 0}
    assert str(join.pk) in view(harness, staff, ministry="4")
    assert str(join.pk) not in view(harness, staff, ministry="9")
    assert str(join.pk) in view(harness, staff)
    # Ministry 4's leader has it; Food pantry's leader no longer does, in
    # the worklist or By Ministry.
    assert str(join.pk) in view(harness, fours)
    assert str(join.pk) not in view(harness, pantry)
    assert summaries(harness, roles=("ministry_leader",), scope=(4,)) == {4: 1}
    assert summaries(harness, roles=("ministry_leader",), scope=(9,)) == {9: 0}
    listed = page(
        harness,
        roles=("ministry_leader",),
        scope=(9,),
        ministry=9,
        state="any",
    )["rows"]
    assert str(join.pk) not in {row["id"] for row in listed}


def test_an_unedited_request_is_unchanged(response_service):
    """Without an Edit each request stays under the Family's Ministry: By
    Ministry, the Ministry filter and each Ministry's leader agree."""
    harness = setup(response_service)
    staff = actor(harness, {"staff"}, ())
    rows = view(harness, staff)
    by_ministry = {
        (row["ministry_duid"], row["action"]): key
        for key, row in rows.items()
        if row.get("latest") is not False
    }
    join, leave = by_ministry[(9, "join")], by_ministry[(4, "leave")]
    assert [
        (row["duid"], row["joining"], row["leaving"])
        for row in page(harness)["summaries"]
    ] == [(4, 0, 1), (9, 1, 0)]
    assert set(view(harness, staff, ministry="9")) == {join}
    assert set(view(harness, staff, ministry="4")) == {leave}
    assert set(view(harness, actor(harness, {"ministry_leader"}, (9,)))) == {join}
    assert set(view(harness, actor(harness, {"ministry_leader"}, (4,)))) == {leave}


def test_the_closing_check_passes_on_the_installed_schema():
    """The check is the one the migration ran; it still holds afterwards."""
    assert sorted(REPLACED) == sorted(EARLIER)
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(ACL)
        cursor.execute(CHECK)
        transaction.set_rollback(True)


@pytest.mark.parametrize("name", sorted(EARLIER))
def test_the_closing_check_refuses_each_earlier_body(name):
    """A selection left on its real 0131 or 0141 body is refused."""
    body = earlier(name)
    assert "(VALUES (r.ministry_duid),(r.reviewed_ministry_duid))" in body
    check_refuses(body)


@pytest.mark.parametrize(
    "name, new, old",
    [
        (
            "stewardship_ministry_report_v3",
            "        ON m.duid=r.ministry_duid\n",
            "        ON m.duid IN (r.ministry_duid,r.family_ministry_duid)\n",
        ),
        (
            "stewardship_ministry_followup_v1",
            "    WHERE CASE WHEN q.reviewed_ministry_duid IS NULL THEN m.duid"
            " ELSE e.duid END\n        IS NOT NULL\n",
            "    WHERE m.duid IS NOT NULL OR e.duid IS NOT NULL\n",
        ),
        (
            "stewardship_ministry_followup_v1",
            "            OR coalesce(r.reviewed_ministry_duid,r.ministry_duid)"
            "::text=(filters->>'ministry'))\n",
            "            OR r.ministry_duid::text=(filters->>'ministry')\n"
            "            OR r.reviewed_ministry_duid::text=(filters->>'ministry'))\n",
        ),
        (
            "stewardship_ministry_packet_v1",
            "(VALUES (coalesce(r.reviewed_ministry_duid,r.ministry_duid))) d(duid)",
            "(VALUES (r.ministry_duid),(r.reviewed_ministry_duid)) d(duid)",
        ),
        (
            "stewardship_ministry_report_v3",
            "x.values->'modules' OPERATOR(pg_catalog.?) 'ministry'",
            "x.values->'modules' ? 'ministry'",
        ),
    ],
)
def test_the_closing_check_refuses_each_attribution_put_back(name, new, old):
    """A body with any one of 0198's changes undone, counting, scoping or
    filtering by both Ministries again or a bare operator, is refused."""
    own = definition(MIGRATION, name)
    assert own.count(new) == 1
    check_refuses(own.replace(new, old))


def test_the_closing_check_refuses_a_definer_body():
    """A selection made SECURITY DEFINER is refused."""
    check_refuses(
        "ALTER FUNCTION public.stewardship_ministry_followup_v1"
        "(uuid, jsonb, boolean, bigint[], uuid, uuid, integer, integer) "
        "SECURITY DEFINER"
    )


def test_the_closing_check_refuses_changed_grants():
    """CREATE OR REPLACE keeps grants; a change to one is refused."""
    check_refuses(
        "REVOKE ALL ON FUNCTION public.stewardship_ministry_packet_v1"
        "(uuid, bigint[], boolean, boolean, bigint[]) FROM PUBLIC",
        "changed the selections' grants",
    )


def test_the_migration_installs_over_the_earlier_definitions():
    """Run whole over the 0131 and 0141 bodies, the frozen file installs and
    checks. Rolled back afterwards, so later tests see the installed schema.
    """
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SET LOCAL check_function_bodies = false")
        for name in EARLIER:
            cursor.execute(earlier(name))
        cursor.execute(MIGRATION)
        transaction.set_rollback(True)
