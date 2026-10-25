"""My review work's counts and page under the real web role (#1690).

Each count is read from the stream's stored state and placed by the shared
pipeline mapping; a Ministry leader's comes from the follow-up selection,
which derives the leader's scope in SQL, so no other Ministry is counted.
Each card's count is its stream's worklist count at that Stage choice, and
a stage the menu counts is the menu's own read (#2101): ``matches`` checks
all three on the same data, here and in the tests that place items at the
later stages (refresh checks, additions, pledges).
"""

import re
from collections import Counter
from types import SimpleNamespace

import pytest
from django.db import transaction
from django.test.utils import CaptureQueriesContext

from parishkit.stewardship.accounts import admin_context, review_work
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
    """``read_counts`` through the web login's real grants, with its read count."""
    from django.db import connection

    with (
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        transaction.atomic(),
        CaptureQueriesContext(connection) as queries,
    ):
        found = read_counts(principal, harness.campaign.pk)
    # The statement runs in a savepoint (so a failed count rolls back alone),
    # whose SAVEPOINT and RELEASE are not reads.
    reads = [q for q in queries if "SAVEPOINT" not in q["sql"].upper()]
    return found, len(reads)


# Each stream's menu entry, as the chrome offers it to Staff.
ENTRIES = {
    "census_changes": Stream.CENSUS,
    "ministry_report": Stream.MINISTRY,
    "limitations_report": Stream.CANNOT_PARTICIPATE,
    "pledges_to_enter": Stream.FINANCIAL,
    "information_queue": Stream.ADDITIONAL_INFORMATION,
}


def worklists(campaign_id, principal):
    """Each stream's worklist counts by Stage choice value, from its own rows.

    The pages' own reads with their default filters: Census changes'
    ``stage_counts``, Ministry requests' Staff rows, Limitations' answers
    with its Cannot contribute rows, Pledges' rows and Additional
    information's requests. A page its campaign does not offer (no
    financial or Ministry module) is left out, as the menu greys it.
    """
    from parishkit.stewardship.reports import (
        information_worklist,
        limitation_worklist,
        ministry_worklist,
    )
    from parishkit.stewardship.reports.census_changes import (
        CensusQuery,
        census_changes,
    )
    from parishkit.stewardship.reports.limitations import (
        LimitationsQuery,
        limitations_report,
    )
    from parishkit.stewardship.reports.pledge_review_views import pledge_rows

    answers = limitation_worklist.answer_rows(
        limitations_report(campaign_id, LimitationsQuery.parse({}), principal)
    )
    pages = {
        Stream.CENSUS: census_changes(
            campaign_id, CensusQuery(stage="to_review"), principal
        )["stage_counts"],
        Stream.CANNOT_PARTICIPATE: limitation_worklist.at_stage(answers, "all")[1],
        Stream.ADDITIONAL_INFORMATION: information_worklist.requests(
            campaign_id, information_worklist.WorklistQuery()
        )[2],
    }
    try:
        pledges, _ = pledge_rows(campaign_id, principal)
        financial = Counter(row["stage"] for row in pledges)
        financial["not_seen"] = sum(row["not_seen"] for row in pledges)
        pages[Stream.FINANCIAL] = dict(financial)
    except PermissionError:
        pass
    try:
        ministry = ministry_worklist.read_rows(
            campaign_id, ministry_worklist.WorklistQuery(), principal
        )
        pages[Stream.MINISTRY] = ministry_worklist.stage_counts(ministry["rows"])
    except PermissionError:
        pass
    return pages


def principal_of(harness, user_id):
    """The signed-in ``Principal`` of portal user ``user_id``, read as web."""
    from parishkit.stewardship.accounts.policy import current_principal

    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        return current_principal(harness.service.store, user_id)


# The most statements the counts may take: the one statement, then each
# stream's refresh-check reads of its items Entered by hand (census 2,
# Ministry requests 4, pledges 2).
QUERY_CAP = 1 + 2 + 4 + 2


def reads(queries):
    """The captured statements that read, without savepoint bookkeeping."""
    return len([q for q in queries if "SAVEPOINT" not in q["sql"].upper()])


def matches(harness, principal):
    """My review work's cards, the menu and each worklist agree (#2101).

    Reads, on one data and under the web login's real grants, the menu's
    open counts and stages (``admin_context._open_counts``), the cards
    (``review_work.panels`` over that menu and ``read_counts``) and each
    stream's worklist counts; asserts every card's count is its worklist's
    count at that Stage choice, that a stream without a Not seen in
    ParishSoft card has nothing flagged, and every menu count its
    worklist's open stages. Admin Home's path (its counts riding on the
    menu's statement, ``with_counts``) must draw the same cards, and
    neither path may take more than ``QUERY_CAP`` statements. Returns each
    stream's cards as ``{label: count}``.
    """
    from django.db import connection

    request = SimpleNamespace()
    flags = review_work.read_flags(principal)
    home_request = SimpleNamespace(_stewardship_review_flags=flags)
    with (
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        transaction.atomic(),
    ):
        pages = worklists(harness.campaign.pk, principal)
        # The menu offers each page the campaign has.
        items = [
            SimpleNamespace(name=name, url=f"/{name}/")
            for name, stream in ENTRIES.items()
            if stream in pages
        ]
        menu = admin_context._open_counts(request, principal, items, harness.campaign)
        with CaptureQueriesContext(connection) as landing:
            counts = read_counts(principal, harness.campaign.pk)
        with CaptureQueriesContext(connection) as home_reads:
            admin_context._open_counts(home_request, principal, items, harness.campaign)
            home = review_work.with_counts(
                home_request, {"flags": flags, "leader": counts["leader"]}
            )
    assert reads(landing) <= QUERY_CAP and reads(home_reads) <= QUERY_CAP
    sections = [{"items": [{"name": item.name, "url": item.url} for item in items]}]
    admin_context.attach_counts(sections, menu, request._stewardship_open_stages)
    chrome = {
        "entries": {
            item.name: SimpleNamespace(name=item.name, url=item.url, reason=None)
            for item in items
        },
        "sections": sections,
    }
    found = {}
    shown = review_work.panels(chrome, {"counts": counts})
    assert review_work.panels(chrome, home) == shown
    for panel in shown:
        stream = Stream(panel.stream)
        found[stream] = {}
        stages = set()
        for card in panel.cards:
            stage = card.url.partition("?stage=")[2]
            stages.add(stage)
            expected = pages[stream].get(stage, 0)
            assert card.count == expected, (stream, stage, card.count, expected)
            found[stream][str(card.label)] = card.count
        if "not_seen" not in stages:
            # A hidden Not seen in ParishSoft card hides nothing flagged.
            assert not pages[stream].get("not_seen"), stream
    census, ministry = pages[Stream.CENSUS], pages.get(Stream.MINISTRY, {})
    for name, expected in (
        (
            "census_changes",
            census.get("to_review", 0) + census.get("conflict", 0),
        ),
        ("ministry_report", ministry.get("to_review", 0)),
        (admin_context.ENTRY_COUNT, ministry.get("to_enter_by_hand", 0)),
        (
            "limitations_report",
            pages[Stream.CANNOT_PARTICIPATE].get("to_review", 0),
        ),
        (
            "information_queue",
            pages[Stream.ADDITIONAL_INFORMATION].get("to_review", 0),
        ),
    ):
        assert menu.get(name, 0) == expected, (name, menu.get(name), expected)
    return found


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


def test_staff_cards_match_the_menu_and_each_worklist(response_service):
    """Staff's cards are each worklist's counts and the menu's (#2101) as
    decisions move the items: an Ignored request and an Accepted answer
    leave To review (an Accepted answer for To handle), a resolved leave is
    To enter by hand, and its tick leaves it. This module's own counts are
    one statement while nothing is Entered by hand, and its refresh-check
    reads only then."""
    from parishkit.stewardship.campaigns.models import FamilyCampaign
    from parishkit.stewardship.pipeline import Action
    from parishkit.stewardship.responses.limitation_review import answer_id
    from parishkit.stewardship.responses.models import AdditionalInformationItem

    from .test_information_limitation_review_postgresql import decide, decide_answer

    harness = response_service
    start(harness)
    harness = activate_response_service(harness)
    submission = submit(harness)
    staff = actor(harness, ("staff",), ())
    found, queries = counts(harness, staff)
    assert queries == 1
    # Nothing the parish added, no financial module (so no pledge), nothing
    # Entered by hand; the other counts are the menu's.
    assert found[Stream.CENSUS] == {} and found[Stream.FINANCIAL] == {}
    assert found[Stream.MINISTRY] == {} and found["leader"] is None
    cards = matches(harness, staff)
    # Both census changes are To review: the By hand one too (decision 2).
    # Not seen in ParishSoft, a Stage choice only while flagged, has no card.
    assert cards[Stream.CENSUS] == {
        "To review": 2,
        "Conflict": 0,
        "To enter by hand": 0,
        "Entered incorrectly": 0,
    }
    # One Member who cannot serve and one Family that cannot attend; the
    # leave is still open.
    assert cards[Stream.CANNOT_PARTICIPATE] == {"To review": 2, "To handle": 0}
    assert cards[Stream.ADDITIONAL_INFORMATION] == {"To review": 1, "To handle": 0}
    assert cards[Stream.MINISTRY] == {"To review": 1, "To enter by hand": 0}
    # No financial module: the menu greys Pledges, so matches leaves it out.
    assert Stream.FINANCIAL not in cards

    # Decisions the cards once ignored (#2101): an Ignored request and an
    # Accepted answer are no longer To review.
    admin = user("admin@example.org").pk
    item = AdditionalInformationItem.objects.get(submission_id=submission.pk)
    decide(harness, admin, Action.IGNORE, item)
    family = FamilyCampaign.objects.get(pk=submission.family_id)
    member = answer_id(family.family_duid, "member", "3")
    decide_answer(harness, admin, Action.ACCEPT, member, 0)
    cards = matches(harness, staff)
    assert cards[Stream.ADDITIONAL_INFORMATION] == {"To review": 0, "To handle": 0}
    assert cards[Stream.CANNOT_PARTICIPATE] == {"To review": 1, "To handle": 1}

    leave = MinistryRequest.objects.exclude(state__in=["superseded", "cancelled"]).get(
        ministry_duid=4
    )
    edit(harness, admin, leave, state="resolved", outcome="leave_confirmed")
    leave.refresh_from_db()
    assert matches(harness, staff)[Stream.MINISTRY] == {
        "To review": 0,
        "To enter by hand": 1,
    }
    tick(harness, admin, leave, True)
    assert matches(harness, staff)[Stream.MINISTRY] == {
        "To review": 0,
        "To enter by hand": 0,
    }
    # The ticked request's checks are read (no check yet, so not seen is 0).
    found, queries = counts(harness, staff)
    assert found[Stream.MINISTRY] == {"not_seen": 0} and queries > 1


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
    count a link card to that stream's page at its Stage (#1818;
    Limitations since #2101), which the page honours; a Stage it does not
    offer is its plain 400, never a 500, and so is any other query on
    Limitations' page."""
    harness = setup(response_service)
    browser, _, _, _ = leader(harness, google, roles=("staff",))
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response, body = get(browser, "/admin/")
    assert response.status_code == 200
    assert b"<h1>My review work</h1>" in body
    # One name: the menu's entry says what the heading says, and it is the
    # first entry of Review work after Review one at a time (#2123), with no
    # Home entry above the groups (R39 finding 4).
    group = re.search(rb'data-menu-group="review".*?</ul>', body, re.S).group(0)
    first, second = re.findall(rb"<li><a [^>]*>([^<]*)</a>", group)[:2]
    assert (first, second) == (b"Review one at a time", b"My review work")
    assert b'aria-current="page">My review work</a>' in group
    assert b">Home</a>" not in body
    # Home's own panels are gone (decision 8: nothing else is above it).
    assert b"data-next-steps" not in body and b"Family participation" not in body
    ministry = panel(body, "ministry")
    assert cards(ministry) == [
        (
            b"/admin/reports/ministries/?stage=to_review",
            b"To review",
            b"2",
            b"requests",
        ),
        (
            b"/admin/reports/ministries/?stage=to_enter_by_hand",
            b"To enter by hand",
            b"0",
            b"requests",
        ),
    ]
    assert cards(panel(body, "census")) == [
        (b"/admin/reports/census/?stage=" + stage, label, b"0", b"changes")
        for stage, label in (
            (b"to_review", b"To review"),
            (b"conflict", b"Conflict"),
            (b"to_enter_by_hand", b"To enter by hand"),
            (b"entered_incorrectly", b"Entered incorrectly"),
        )
    ]
    # Limitations links its Stage too since #2101.
    assert cards(panel(body, "cannot_participate")) == [
        (b"/admin/reports/limitations/?stage=" + stage, label, b"0", b"answers")
        for stage, label in ((b"to_review", b"To review"), (b"to_handle", b"To handle"))
    ]
    assert cards(panel(body, "additional_information")) == [
        (b"/admin/reports/information/?stage=" + stage, label, b"0", b"items")
        for stage, label in ((b"to_review", b"To review"), (b"to_handle", b"To handle"))
    ]
    # Each linked page opens for Staff, at the card's Stage.
    hrefs = {href.decode() for href, *_ in cards(body)}
    for href in hrefs:
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            response, page = get(browser, href)
        assert response.status_code == 200, href
        stage = href.partition("?stage=")[2]
        if stage:
            assert re.search(
                rb'<option value="' + stage.encode() + rb'"[^>]*\bselected\b', page
            ), href
    for page in {href.partition("?")[0] for href in hrefs}:
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            assert get(browser, page + "?stage=nope")[0].status_code == 400, page
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        refused = get(browser, "/admin/reports/limitations/?stage=to_review&show=any")
    assert refused[0].status_code == 400
    # Limitations' address carries its Stage, so a reload or bookmark after an
    # in-place Stage change draws that Stage; To review's is the plain address.
    route = "/admin/reports/limitations/"
    for stage, expected in (
        ("to_handle", route + "?stage=to_handle"),
        ("to_review", route),
    ):
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            _, page = get(browser, f"{route}?stage={stage}")
        assert f'<a href="{expected}" data-page-address'.encode() in page, stage


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
    assert b'aria-current="page">Home</a>' in body
    assert b">My review work</a>" not in body
    census = panel(body, "census")
    assert (
        b"/admin/reports/census/update-parishsoft/",
        b"Ready to update",
        b"0",
        b"changes",
    ) in cards(census)
