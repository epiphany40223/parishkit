"""Campaign-end Next steps and menu counts under the real web role (#1590).

Home offers Administrators and Staff Census changes, Ministry requests to
enter by hand, Additional information and Ministry requests, each with its
count, and each count held equal to its page's own row selection: Census
changes' To review and Conflict rows, the same for every role
(Administrator decision, 2026-10-18), and Ministry requests' To enter by
hand Stage (which holds the roster changes since Roster changes to enter
was folded into it, R39 finding 2). A Ministry leader gets neither count.
"""

import re
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.db import connection, transaction

from parishkit.stewardship.accounts import admin_context
from parishkit.stewardship.accounts.policy import Principal
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.reports.ministry_roster_views import roster_rows
from parishkit.stewardship.reports.ministry_worklist import WorklistQuery, read_rows
from parishkit.stewardship.workflows.roster import can_tick

from .response_builders import activate_response_service
from .test_background_grants_postgresql import task_login
from .test_census_changes_postgresql import ADMIN, LEADER, STAFF, submit_live, worklist
from .test_census_conflict_postgresql import conflicted
from .test_census_pipeline_review_postgresql import decide, prepared
from .test_census_review_postgresql import fresh
from .test_ministry_exports_postgresql import leader
from .test_ministry_followup_postgresql import _menu_count, edit, member_gone
from .test_ministry_reports_postgresql import actor
from .test_ministry_responses_postgresql import start
from .test_report_workspace_postgresql import read
from .test_roster_entries_postgresql import resolved, tick

pytestmark = pytest.mark.django_db(transaction=True)

CENSUS = [SimpleNamespace(name="census_changes", url="/admin/reports/census/")]
ROSTER_URL = "/admin/reports/ministries/?stage=to_enter_by_hand"
OLD_URL = "/admin/reports/ministries/follow-up/roster/"
ROSTER = [SimpleNamespace(name="ministry_report", url="/admin/reports/ministries/")]


def census_count(harness, principal, items=CENSUS):
    """The menu's Census changes count for ``principal``, on the web login."""
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        return admin_context._open_counts(
            SimpleNamespace(), principal, items, harness.campaign
        ).get("census_changes")


def roster_count(principal, campaign):
    """Home's Ministry requests to enter by hand count, always asked for, on
    the web login (only Administrators and Staff get it; asking anyway
    shows the count's own checks)."""
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        return admin_context._open_counts(
            SimpleNamespace(), principal, ROSTER, campaign
        ).get(admin_context.ENTRY_COUNT)


def to_enter(campaign, principal):
    """How many rows Ministry requests lists at To enter by hand, as the page
    reads them, on the web login."""
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        rows = read_rows(campaign.pk, WorklistQuery(), principal)["rows"]
    return sum(row["stage"] == "to_enter_by_hand" for row in rows)


def steps(body):
    """Home's Next steps cards (#1673), each card's text with tags stripped
    and spaces collapsed, in order."""
    return [
        " ".join(re.sub(r"<[^>]+>", " ", item).split())
        for item in re.findall(
            r"<li [^>]*data-link-card>(.*?)</li>", body.decode(), re.S
        )
    ]


def test_census_count_is_the_same_for_both_roles(response_service):
    """Staff and an Administrator count the worklist's To review and Conflict
    stages (Administrator decision, 2026-10-18); a Ministry leader, whom the
    page refuses, gets no count even when asked."""
    harness = response_service
    start(harness)
    harness = activate_response_service(harness)
    submit_live(harness)
    # One By hand Family address and one automatic rename, both To review.
    for principal in (STAFF, ADMIN):
        assert len(worklist(harness, principal, stage="to_review")["rows"]) == 2
        assert census_count(harness, principal) == 2
    with pytest.raises(PermissionError):
        worklist(harness, LEADER)
    assert census_count(harness, LEADER) is None


def test_census_count_is_to_review_and_conflict_only(response_service):
    """The count follows each change's stage: a Conflict still counts, an
    accepted change (To enter by hand or Ready to update) no longer does,
    for Staff and an Administrator alike (Administrator decision,
    2026-10-18)."""
    harness, _, staff, rows = prepared(response_service)

    def counts():
        """The menu's count for each role, held equal to the page's stages."""
        shown = {census_count(harness, principal) for principal in (STAFF, ADMIN)}
        assert len(shown) == 1
        listed = sum(
            len(worklist(harness, ADMIN, stage=stage)["rows"])
            for stage in ("to_review", "conflict")
        )
        assert shown == {listed}
        return listed

    # Four changes, all To review.
    assert counts() == 4
    # A Conflict still waits on a reviewer.
    conflicted(rows["first_name"])
    assert counts() == 4
    # Accepting the By hand prefix moves it to To enter by hand.
    decide(harness, staff, [rows["prefix"]], "accept")
    assert counts() == 3
    # Accepting the Conflict moves it to Ready to update, which Publish to
    # ParishSoft owns.
    decide(harness, staff, [fresh(rows["first_name"])], "accept")
    assert counts() == 2


def test_home_and_menu_show_the_census_count_for_an_administrator(
    response_service, google
):
    """Signed in, the menu and Home's Next steps carry the same Census
    changes count, To review and Conflict, as "N to do"."""
    harness = response_service
    start(harness)
    harness = activate_response_service(harness)
    submit_live(harness)
    browser, _, _, _ = leader(harness, google, roles=("administrator",))
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response, body = read(browser, "/admin/")
    assert response.status_code == 200
    expected = len(worklist(harness, ADMIN, stage="to_review")["rows"])
    assert expected == 2
    assert _menu_count(body, "Census changes") == expected
    assert f"Census changes {expected} to do" in steps(body)
    assert 'href="/admin/reports/census/"' in body.decode()


def test_roster_count_is_the_lists_not_yet_entered_rows(response_service):
    """The To enter by hand count follows the list and Ministry requests'
    Stage: one resolved join to enter, none once it is ticked, again one
    once cleared. A Ministry leader gets no count."""
    harness, admin, join, _ = resolved(response_service)
    staff = actor(harness, ("staff",), ())
    administrator = actor(harness, ("administrator",), ())
    own = actor(harness, ("ministry_leader",), (9,))
    campaign = Campaign.objects.select_related("active_configuration").get(
        pk=harness.campaign.pk
    )

    def counts(principal):
        """The list's default rows, the Stage's rows and the count."""
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            rows, _ = roster_rows(campaign.pk, principal, "todo")
        return (
            len(rows),
            to_enter(campaign, principal),
            roster_count(principal, campaign),
        )

    for principal in (staff, administrator):
        assert counts(principal) == (1, 1, 1)
    tick(harness, admin, join, True)
    assert counts(staff) == (0, 0, 0)
    tick(harness, admin, join, False)
    assert counts(staff) == (1, 1, 1)
    # The page admits Administrators and Staff only (its _principal).
    assert not can_tick(own)
    assert roster_count(own, campaign) is None
    # Python claiming Staff for a portal user SQL does not know reads
    # nothing: the selection refuses, and the menu shows no number.
    stranger = Principal(uuid4(), frozenset({"staff"}))
    assert roster_count(stranger, campaign) is None
    # A draft has nothing to enter yet: the menu does not read the count.
    draft = SimpleNamespace(pk=campaign.pk, state="draft")
    assert roster_count(staff, draft) is None


def agree(harness):
    """A function returning the roster list's rows, Ministry requests' To
    enter by hand rows and Home's count for Staff, which must always be
    equal (#1660, R39 finding 2), on the web login."""
    staff = actor(harness, ("staff",), ())
    campaign = Campaign.objects.select_related("active_configuration").get(
        pk=harness.campaign.pk
    )

    def counts():
        """One number if the list, the Stage and the count agree, else all."""
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            rows, _ = roster_rows(campaign.pk, staff, "todo")
        found = {len(rows), to_enter(campaign, staff), roster_count(staff, campaign)}
        return (found.pop(),) * 2 if len(found) == 1 else tuple(found)

    return counts


def test_roster_count_includes_a_confirmed_leave(response_service):
    """A resolved leave is a roster change too: it counts as the page lists
    it, and a tick removes it from both."""
    harness, admin, join, leave = resolved(response_service)
    counts = agree(harness)
    assert counts() == (1, 1)
    edit(harness, admin, leave, state="resolved", outcome="leave_confirmed")
    leave.refresh_from_db()
    assert counts() == (2, 2)
    tick(harness, admin, leave, True)
    assert counts() == (1, 1)


def test_roster_count_leaves_out_a_declined_request(response_service):
    """A declined request is no roster change, so neither the page nor the
    count lists it (the outcome filter, not the tick)."""
    harness, admin, join, leave = resolved(response_service)
    counts = agree(harness)
    edit(harness, admin, leave, state="resolved", outcome="declined")
    assert counts() == (1, 1)


def test_roster_count_leaves_out_a_request_parishsoft_resolved(response_service):
    """A join the ParishSoft roster already resolved needs no entering, on
    the page and in the menu's count."""
    from parishkit.stewardship.source.models import SourceCurrent

    harness, admin, join, _ = resolved(response_service)
    counts = agree(harness)
    assert counts() == (1, 1)
    snapshot = SourceCurrent.objects.values_list("snapshot_id", flat=True).get()
    with transaction.atomic(), connection.cursor() as cursor:
        # As a refresh would mark it (test_roster_entries_postgresql).
        cursor.execute("SET LOCAL session_replication_role = replica")
        cursor.execute(
            "UPDATE stewardship_ministry_request SET resolution_source_id=%s,"
            " version=version+1 WHERE id=%s",
            [snapshot, join.pk],
        )
    assert counts() == (0, 0)


def test_roster_count_keeps_a_change_for_a_member_who_is_gone(response_service):
    """A Member ParishSoft no longer lists in the Family still needs their
    change entered: the page keeps the row and so does the count."""
    harness, admin, join, _ = resolved(response_service)
    counts = agree(harness)
    for standing in ("moved", "missing"):
        member_gone(harness, standing)
        assert counts() == (1, 1)


def test_roster_count_ignores_the_request_a_resubmission_adds(response_service):
    """A Family answering again adds an open request for the same join; the
    resolved one stays, and only it counts, as on the page."""
    from .test_ministry_responses_postgresql import respond, revisit
    from .test_response_http_postgresql import answers_for
    from .test_runtime_auth_grants_postgresql import web_login

    harness, _, join, _ = resolved(response_service)
    counts = agree(harness)
    with web_login():
        form = revisit(harness)
        answers = answers_for(form)
        answers["ministries"]["members"]["3"] = {"join": [9], "leave": []}
        respond(harness, form, answers)
    join.refresh_from_db()
    assert join.state == "resolved"
    assert counts() == (1, 1)


def test_staff_home_lists_the_campaign_end_work_with_counts(response_service, google):
    """Staff see the four work lists after the Response dashboard, each with
    the count of the rows its link opens; a leader sees only Ministry
    requests with its open count."""
    harness, _, _, _ = resolved(response_service)
    browser, _, _, _ = leader(harness, google, roles=("staff",))
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response, body = read(browser, "/admin/")
    assert response.status_code == 200
    listed = steps(body)
    assert [re.sub(r" \d.*", "", step) for step in listed] == [
        "Census changes",
        "Ministry requests to enter by hand",
        "Additional information",
        "Ministry requests",
    ]
    assert "Ministry requests to enter by hand 1 to enter by hand" in listed
    # The harness's leave request is still open; its join was resolved.
    # The menu count stays Ministry requests' own (R39 finding 2).
    assert "Ministry requests 1 open" in listed
    assert _menu_count(body, "Ministry requests") == 1
    assert f'href="{ROSTER_URL}"' in body.decode()
    assert "Roster changes to enter" not in body.decode()


def test_the_to_enter_link_opens_ministry_requests_at_its_stage(
    response_service, google
):
    """Home's link opens Ministry requests at To enter by hand, its menu entry
    current under Review work; the retired Roster changes to enter address is
    gone, with no redirect (R39 finding 2)."""
    harness, _, _, _ = resolved(response_service)
    browser, _, _, _ = leader(harness, google, roles=("staff",))
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response, body = read(browser, ROSTER_URL)
        old, _ = read(browser, OLD_URL)
    assert response.status_code == 200 and old.status_code == 404
    text = body.decode()
    assert '<a href="/admin/reports/ministries/" aria-current="page">' in text
    assert "To enter by hand (1)" in text
    trail = re.search(r'<nav[^>]*aria-label="Breadcrumb".*?</nav>', text, re.S)
    assert trail is not None and "Review work" in trail.group(0)


def test_count_database_error_leaves_the_link_without_a_number(
    response_service, google, monkeypatch, caplog
):
    """A database error in the menu's counts statement does not fail Home:
    the to-enter step still links to its Stage with no number, and the failure
    is logged at WARNING as report_shaping_failed."""
    harness, _, _, _ = resolved(response_service)
    browser, _, _, _ = leader(harness, google, roles=("staff",))
    # Fail the statement the way a lost connection or bad SQL would.
    monkeypatch.setattr(admin_context, "OPEN_COUNTS", "SELECT missing_column")
    with (
        caplog.at_level("WARNING", logger="parishkit.stewardship"),
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
    ):
        response, body = read(browser, "/admin/")
    assert response.status_code == 200
    assert "Ministry requests to enter by hand" in steps(body)
    assert _menu_count(body, "Ministry requests") is None
    assert f'href="{ROSTER_URL}"' in body.decode()
    (warning,) = [
        r for r in caplog.records if r.getMessage() == "report_shaping_failed"
    ]
    assert warning.levelname == "WARNING"
