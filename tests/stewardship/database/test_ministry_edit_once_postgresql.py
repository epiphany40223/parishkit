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
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction
from django.urls import reverse

from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.reports.ministry_followup import FollowupQuery, followup_page
from parishkit.stewardship.workflows.models import (
    MinistryRequest,
    MinistryWorkflowRevision,
)

from .test_background_grants_postgresql import task_login
from .test_hide_gone_members_postgresql import SCHEMA_FILES, definition
from .test_information_followup_postgresql import search
from .test_live_ministries_postgresql import applied, select
from .test_ministry_exports_postgresql import leader
from .test_ministry_followup_postgresql import member_gone
from .test_ministry_followup_postgresql import read as followup
from .test_ministry_reports_postgresql import actor, page, setup
from .test_ministry_responses_postgresql import respond, revisit
from .test_ministry_review_postgresql import decide, view, withdraw_leave
from .test_response_http_postgresql import answers_for
from .test_reviewed_form_postgresql import (
    again,
    current_requests,
    decide_request,
    ministry_setup,
    set_choices,
)

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


def history(harness, ministry):
    """The ids a leader of ``ministry`` finds in its By Ministry list with
    the history option: every version, the replaced ones included."""
    rows = page(
        harness,
        roles=("ministry_leader",),
        scope=(ministry,),
        ministry=ministry,
        state="any",
        history="all",
    )["rows"]
    return {row["id"] for row in rows}


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
    # The join's earlier version, from the first response, which the
    # Family's second answer replaced.
    earlier_join = str(
        MinistryRequest.objects.get(
            ministry_duid=9, action="join", state="superseded"
        ).pk
    )
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
    # With the history option, the earlier version from before the Edit
    # stays under the Family's Ministry, its Ministry in force then; the
    # edited one is listed under Ministry 4 only.
    assert earlier_join in history(harness, 9)
    assert str(join.pk) not in history(harness, 9)
    assert str(join.pk) in history(harness, 4)
    assert earlier_join not in history(harness, 4)

    # Back to review clears the Edit: the request returns to Food pantry
    # for the count, the filter and each leader's scope.
    decide(harness, staff, "back_to_review", join)
    assert (join.state, join.reviewed_ministry_duid) == ("in_progress", None)
    assert summaries(harness) == {4: 0, 9: 1}
    assert str(join.pk) in view(harness, staff, ministry="9")
    assert str(join.pk) not in view(harness, staff, ministry="4")
    assert str(join.pk) in view(harness, pantry)
    assert str(join.pk) not in view(harness, fours)


def test_a_family_resubmission_returns_an_edit_to_the_family_s_ministry(
    response_service,
):
    """Staff edit the Food pantry (9) join into Choir (11), where it is
    counted; the Family then chooses Food pantry again (rule 2), which
    replaces the edited request with one To review under Food pantry, so
    the count and the filter follow it back."""
    harness, staff = ministry_setup(response_service)
    join = current_requests()[9, "join"]
    decide_request(harness, staff, "edit", join, value="11")
    assert summaries(harness) == {4: 0, 9: 0, 11: 1}
    assert str(join.pk) in view(harness, staff, ministry="11")
    again(harness, set_choices([9], [4]))
    join.refresh_from_db()
    successor = str(join.superseded_by_id)
    assert summaries(harness) == {4: 0, 9: 1, 11: 0}
    assert successor in view(harness, staff, ministry="9")
    assert not set(view(harness, staff, ministry="11")) & {successor, str(join.pk)}


def test_the_presence_filters_hold_only_the_ministry_in_force(response_service):
    """Staff edit the Food pantry (9) join into Ministry 4, the campaign
    drops Food pantry, and Member 3 moves out of the Family in ParishSoft.
    Staff's two views that still list Members no longer in ParishSoft, the
    Status choice (presence 'gone') and the roster rows (presence 'any'),
    list the edited request under Ministry 4 and no longer offer Food
    pantry, which nothing in force holds."""
    harness = setup(response_service)
    staff = actor(harness, {"staff"}, ())
    join = withdraw_leave(harness)
    decide(harness, staff, "edit", join, fields={"value": ["4"]})
    assert applied(select(harness, [4]))
    member_gone(harness, "moved")
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        every = followup_page(
            harness.campaign.pk, FollowupQuery(state="any"), staff, every_member=True
        )
    for queue in (followup(harness, staff, state="not_in_parishsoft"), every):
        assert {row["duid"] for row in queue["ministries"]} == {4}
        (row,) = [row for row in queue["rows"] if row["id"] == str(join.pk)]
        assert row["reviewed_ministry_duid"] == 4


@pytest.mark.parametrize("into_pantry", [False, True])
def test_a_refused_update_is_scoped_by_the_ministry_in_force(
    response_service, google, into_pantry
):
    """A refused follow-up save checks scope before its conflict, by the
    Ministry in force. Food pantry's (9) leader posts a refused save for an
    edited (so resolved, no longer open) request. Edited out of Food pantry,
    it is the same denial as an unknown request (403), not a 409 that
    would show it exists; edited into Food pantry, it is theirs, so the
    stale form is the conflict (409)."""
    harness = setup(response_service)
    browser, _, _, _ = leader(harness, google)
    staff = actor(harness, {"staff"}, ())
    if into_pantry:
        form = revisit(harness)
        answers = answers_for(form)
        answers["ministries"]["members"]["3"] = {"join": [], "leave": [4]}
        respond(harness, form, answers)
        target = MinistryRequest.objects.get(
            ministry_duid=4, action="leave", state="new"
        )
        decide(harness, staff, "edit", target, fields={"value": ["9"]})
    else:
        target = withdraw_leave(harness)
        decide(harness, staff, "edit", target, fields={"value": ["4"]})
    revisions = MinistryWorkflowRevision.objects.count()
    refused = {
        "expected_version": str(target.version),
        "request_key": str(uuid4()),
        "state": "resolved",
        "notes": "",
        "contact_channel": "",
    }
    update = reverse("admin:ministry_followup_update", args=[target.pk])
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response, body = search(browser, update, refused)
    assert response.status_code == (409 if into_pantry else 403)
    assert (b"This request changed" in body) is into_pantry
    assert MinistryWorkflowRevision.objects.count() == revisions


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
