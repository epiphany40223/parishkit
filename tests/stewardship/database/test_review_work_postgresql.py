"""My review work's counts and page under the real web role (#1690).

Each count is read from the stream's stored state and placed by the shared
pipeline mapping; a Ministry leader's comes from the follow-up selection,
which derives the leader's scope in SQL, so no other Ministry is counted.
"""

import re

import pytest
from django.db import transaction
from django.test.utils import CaptureQueriesContext

from parishkit.stewardship.accounts.review_work import read_counts
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.pipeline import Stream
from parishkit.stewardship.workflows.models import MinistryRequest

from ..census_factory import address
from .response_builders import activate_response_service
from .test_background_grants_postgresql import task_login
from .test_ministry_exports_postgresql import leader
from .test_ministry_followup_postgresql import edit
from .test_ministry_reports_postgresql import actor, setup
from .test_ministry_responses_postgresql import respond, start
from .test_policy_postgresql import user
from .test_report_workspace_postgresql import read as get
from .test_response_http_postgresql import answers_for, load_form
from .test_roster_entries_postgresql import tick
from .test_runtime_auth_grants_postgresql import web_login

pytestmark = pytest.mark.django_db(transaction=True)


def counts(harness, principal):
    """``read_counts`` through the web login's real grants, with its query count."""
    from django.db import connection

    with (
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        transaction.atomic(),
        CaptureQueriesContext(connection) as queries,
    ):
        return read_counts(principal, harness.campaign.pk), len(queries)


def submit(harness):
    """One live response with something for every Staff stream: a renamed
    Member (an automatic census change) and a new home address (By hand),
    Member 3 cannot serve and the Family cannot attend, Member 3 leaves
    Ministry 4, and a free-text note."""
    with web_login():
        form = load_form(harness)
        answers = answers_for(form)
        answers["members"]["3"]["first_name"] = "Requested"
        answers["family"]["home_address"] = address(line1="9 Census Way")
        answers["cannot_attend"] = True
        answers["service"] = {
            "members": {"3": {"cannot_serve": True, "talents": {}}},
            "proposed_members": {},
        }
        answers["ministries"]["members"]["3"] = {"join": [], "leave": [4]}
        answers["additional_information"] = "Please call about the parish picnic."
        return respond(harness, form, answers)


def test_staff_counts_each_stream_by_stage_in_one_statement(response_service):
    """Staff get every stream's stages from one statement; a resolved leave
    moves to To enter by hand, and its tick to Entered by hand."""
    harness = response_service
    start(harness)
    harness = activate_response_service(harness)
    submit(harness)
    staff = actor(harness, ("staff",), ())
    found, queries = counts(harness, staff)
    assert queries == 1
    # Both census changes are To review: the By hand one too (decision 2).
    assert found[Stream.CENSUS] == {"to_review": 2}
    # One Member who cannot serve and one Family that cannot attend.
    assert found[Stream.CANNOT_PARTICIPATE] == {"to_review": 2}
    assert found[Stream.ADDITIONAL_INFORMATION] == {"to_review": 1}
    # The leave is still open, so nothing is ready to enter by hand (its To
    # review count is the menu's, not read here); the
    # campaign has no financial module, so no answer is a pledge.
    assert found[Stream.MINISTRY] == {}
    assert found[Stream.FINANCIAL] == {}
    assert found["leader"] is None

    admin = user("admin@example.org").pk
    leave = MinistryRequest.objects.exclude(state__in=["superseded", "cancelled"]).get(
        ministry_duid=4
    )
    edit(harness, admin, leave, state="resolved", outcome="leave_confirmed")
    leave.refresh_from_db()
    assert counts(harness, staff)[0][Stream.MINISTRY] == {"to_enter_by_hand": 1}
    tick(harness, admin, leave, True)
    assert counts(harness, staff)[0][Stream.MINISTRY] == {"entered_by_hand": 1}


def test_a_leader_counts_only_the_ministries_they_lead(response_service):
    """A leader reads no Staff stream, and only their own Ministry's requests."""
    harness = setup(response_service)  # A join for Ministry 9, a leave for 4.
    found, _ = counts(harness, actor(harness, ("ministry_leader",), (9,)))
    assert set(found) == {"leader"}
    assert [
        (m["duid"], m["join"], m["leave"]) for m in found["leader"]["ministries"]
    ] == [(9, 1, 0)]
    other, _ = counts(harness, actor(harness, ("ministry_leader",), (4,)))
    assert [
        (m["duid"], m["join"], m["leave"]) for m in other["leader"]["ministries"]
    ] == [(4, 0, 1)]


def cards(panel):
    """(label, href, count, note) of each link card in one panel."""
    return re.findall(
        rb'<a href="([^"]+)"><span class="link-card-label">([^<]+)</span> '
        rb'<span class="link-card-count">([0-9,]+)</span> '
        rb'<span class="link-card-note">([^<]+)</span></a>',
        panel,
    )


def panel(body, stream):
    """The page's panel for one stream, or None."""
    found = re.search(
        rb'<section[^>]*data-review-stream="' + stream.encode() + rb'".*?</section>',
        body,
        re.S,
    )
    return found and found[0]


def test_staff_land_on_my_review_work_with_linked_counts(response_service, google):
    """Staff sign in to My review work, not Home: one panel per stream, each
    count a link card to that stream's page."""
    harness = setup(response_service)
    browser, _, _, _ = leader(harness, google, roles=("staff",))
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response, body = get(browser, "/admin/")
    assert response.status_code == 200
    assert b"<h1>My review work</h1>" in body
    # Home's own panels are gone (decision 8: nothing else is above it).
    assert b"data-next-steps" not in body and b"Family participation" not in body
    ministry = panel(body, "ministry")
    assert cards(ministry) == [
        (b"/admin/reports/ministries/", b"To review", b"2", b"requests"),
        (
            b"/admin/reports/ministries/follow-up/roster/",
            b"To enter by hand",
            b"0",
            b"requests",
        ),
    ]
    assert cards(panel(body, "census")) == [
        (b"/admin/reports/census/", label, b"0", b"changes")
        for label in (b"To review", b"Conflict", b"To enter by hand")
    ]
    assert cards(panel(body, "additional_information")) == [
        (b"/admin/reports/information/", label, b"0", b"items")
        for label in (b"To review", b"To handle")
    ]
    # Each linked page opens for Staff.
    for href in {href for href, *_ in cards(body)}:
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            assert get(browser, href.decode())[0].status_code == 200, href


def test_an_administrator_keeps_home(response_service, google):
    """An Administrator, who also holds the Staff and leader roles, keeps Home,
    with the same counts as its Review work panel."""
    harness = setup(response_service)
    browser, _, _, _ = leader(harness, google, roles=("administrator",))
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response, body = get(browser, "/admin/")
    assert response.status_code == 200
    assert b"<h1>Home</h1>" in body and b"<h1>My review work</h1>" not in body
    assert b"data-review-work" in body and b'data-review-stream="ministry"' in body
