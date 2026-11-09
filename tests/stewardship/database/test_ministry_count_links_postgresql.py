"""Ministry requests (#1133): the review worklist (#1684) with its Stage
counts and By Ministry counts, under the real web role and SQL scope.

Each By Ministry count links to the same page with its Ministry and the
worklist's own closed Request and Stage values, so the view it opens lists
exactly the requests counted; only Ministries the reader may follow up get
links, and a zero count is plain text. Staff and Administrators get a
table; a Ministry leader's page has no By Ministry and a short page help
(#2395), and the same addresses still filter its table. The Stage
choices count what the other filters select, within the reader's scope,
and never a request for a Member no longer in ParishSoft.
"""

import re
from uuid import UUID, uuid4

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from parishkit.stewardship.accounts.policy_models import PortalUser
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.reports import (
    ministry_followup,
    ministry_followup_views,
    ministry_worklist,
)
from parishkit.stewardship.web.tables import PAGE_SIZES
from parishkit.stewardship.workflows.models import MinistryRequest

from ..policy_factory import address
from .auth_builders import signed_in
from .campaign_builders import change
from .response_builders import activate_response_service
from .test_background_grants_postgresql import task_login
from .test_information_followup_postgresql import search
from .test_ministry_exports_postgresql import leader
from .test_ministry_followup_postgresql import edit, member_gone, requests
from .test_ministry_reports_postgresql import prepare, setup
from .test_ministry_responses_postgresql import configure, ministry_source, respond
from .test_report_workspace_postgresql import read as get
from .test_response_http_postgresql import answers_for, load_form
from .test_source_families_postgresql import promote

pytestmark = pytest.mark.django_db(transaction=True)

PAGE = "/admin/reports/ministries/"
ITEMS = "/admin/reports/ministries/follow-up/"
# The statements a filtered Staff view adds for its roster counts (#2331):
# one read_rows of the default view (its follow-up read, roster marks,
# checks and holds).
EXTRA = 6
JOIN_9 = f"{PAGE}?ministry=9&kind=join&stage=all"
# One sentence of each page help (#2395): a Ministry leader's short one,
# and Staff's full one.
LEADER_HELP = "the Ministries you lead. Stage chooses which ones show"
STAFF_HELP = "Live join and leave requests for the Ministries you may follow up"
LEAVE_4 = f"{PAGE}?ministry=4&kind=leave&stage=all"


def page(browser, path):
    """One page's body as text, after checking it was shown."""
    response, body = get(browser, path)
    assert response.status_code == 200, path
    return body.decode()


def listed(body):
    """The request ids the queue table lists, from each row's request link."""
    return {UUID(pk) for pk in re.findall(rf"{ITEMS}([0-9a-f-]{{36}})/\?queue=", body)}


def href(address):
    """A count's address as it appears, escaped, in an href: it names the
    queue table's region, which the page redraws in place."""
    return f'href="{address.replace("&", "&amp;")}#table"'


def selected(body, select_id):
    """The selected option's value in one of the queue's filter controls."""
    control = body.split(f'id="{select_id}"', 1)[1].split("</select>", 1)[0]
    match = re.search(r'<option value="([^"]*)" selected>', control)
    return match[1] if match else None


def by_ministry(body):
    """The By Ministry section's HTML."""
    section = body.split('<section id="by-ministry"', 1)[1]
    return section.split("</section>", 1)[0]


def stages(body):
    """The Stage filter's choices with their counts (#1684); the page has no
    totals or chart any more (#1676)."""
    assert 'id="ministry-totals"' not in body and "data-chart=" not in body
    control = body.split('id="requests-stage"', 1)[1].split("</select>", 1)[0]
    return re.findall(r"<option [^>]*>([^<]*)</option>", control)


def signed_in_as(harness, google, email, roles):
    """Sign in ``email`` through an exact-address rule with ``roles``."""
    store = harness.service.store
    change(
        store,
        store.active(),
        uuid4(),
        [{"operation": "add", "section": "login_rules", **address(email, roles=roles)}],
    )
    google[0]["email"] = email
    browser, login = signed_in()
    assert login.status_code == 302
    return browser, PortalUser.objects.get(google_subject=google[0]["sub"]).pk


def test_a_leaders_page_has_no_by_ministry_and_its_addresses_filter_the_table(
    response_service, google
):
    """A leader of Food pantry (9) gets a short page help and no By Ministry
    (#2395); a count's address, which My review work's card and a passed-on
    link still carry, opens the table with its filters shown and lists
    exactly the requests counted, resolved requests included in the join
    list; only closed filter values in scope may travel."""
    harness = setup(response_service)
    browser, head, _, _ = leader(harness, google)
    join, leave = requests()  # A join for Ministry 9, a leave for Ministry 4.
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        body = page(browser, PAGE)
        assert 'id="by-ministry"' not in body and "remember-open-v1.js" not in body
        assert LEADER_HELP in body and STAFF_HELP not in body
        # Ministry 4 is not this leader's.
        assert "ministry=4" not in body
        # The default view lists the open join.
        assert listed(body) == {join.pk}
        queue = page(browser, JOIN_9)
        assert listed(queue) == {join.pk}
        assert selected(queue, "filter-ministry") == "9"
        assert selected(queue, "filter-action") == "join"
        assert selected(queue, "requests-stage") == "all"
        assert listed(page(browser, f"{PAGE}?ministry=9")) == {join.pk}

    # Resolved, the request is still listed as joining but no longer to
    # review.
    edit(harness, head, join, state="resolved", outcome="joined")
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        assert listed(page(browser, f"{PAGE}?ministry=9")) == set()
        assert listed(page(browser, JOIN_9)) == {join.pk}
        for refused, status in (
            (LEAVE_4, 403),
            (f"{PAGE}?kind=join", 400),
            (f"{PAGE}?ministry=9&kind=maybe", 400),
            (f"{PAGE}?ministry=9&stage=all&search=x", 400),
            (f"{PAGE}?ministry=9&kind=join&queue=abc", 400),
        ):
            assert get(browser, refused)[0].status_code == status, refused
    assert leave.pk not in listed(queue)


def test_a_leader_of_several_ministries_gets_no_by_ministry(response_service, google):
    """A leader of Choir (4) and Food pantry (9) gets no By Ministry either
    (#2395), no Admin/Staff marks (#1135) and no Follow-up packet (#2394);
    the Ministry filter offers both Ministries, and each one's address
    filters the table."""
    harness = setup(response_service)
    browser, _ = signed_in_as(
        harness, google, "leader-4-9@example.org", ["ministry_leader"]
    )
    join, leave = requests()
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        body = page(browser, PAGE)
        assert 'id="by-ministry"' not in body and LEADER_HELP in body
        assert "Not in ParishSoft" not in body
        assert "ministry-packet" not in body
        choices = body.split('<select id="filter-ministry"', 1)[1].split("</select>")[0]
        assert '<option value="4">Choir' in choices
        assert '<option value="9">Food pantry' in choices
        assert listed(body) == {join.pk, leave.pk}
        assert listed(page(browser, LEAVE_4)) == {leave.pk}
        assert stages(body)[:2] == ["To review (2)", "Accepted (0)"]


@pytest.mark.parametrize("role", ["staff", "administrator"])
def test_staff_and_administrators_count_every_ministry(response_service, google, role):
    """Staff and Administrators follow up every Ministry, so each count that
    is not zero links; they keep the full page help (#2395); the old queue
    address is gone, with no redirect."""
    harness = setup(response_service)
    browser, _, _, _ = leader(harness, google, roles=(role,))
    join, leave = requests()
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        body = page(browser, PAGE)
        assert STAFF_HELP in body and LEADER_HELP not in body
        section = by_ministry(body)
        # Every link's accessible name names its Ministry.
        assert 'aria-label="1 join request for Food pantry"' in section
        assert href(JOIN_9) in section and href(LEAVE_4) in section
        assert href(f"{PAGE}?ministry=4") in section
        assert listed(page(browser, LEAVE_4)) == {leave.pk}
        assert join.pk not in listed(page(browser, LEAVE_4))
        assert get(browser, ITEMS)[0].status_code == 404


@pytest.mark.parametrize("roles, expected", [(None, "1"), (("staff",), "2")])
def test_totals_add_up_only_the_readers_requests(
    response_service, google, roles, expected
):
    """The Stage counts come from the follow-up selection's own scope
    (#1133, #1684): a leader of Food pantry (9) counts its join request
    alone, while Staff count Ministry 4's leave request too."""
    harness = setup(response_service)
    browser, _, _, _ = leader(harness, google, **({"roles": roles} if roles else {}))
    requests()  # A join for Ministry 9, a leave for Ministry 4.
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        assert stages(page(browser, PAGE))[0] == f"To review ({expected})"


def three_ministries(harness):
    """Choir (4), Food pantry (9) and Hospitality (11) in the campaign, with
    one live response asking Member 3 to join 9 and 11 and to leave 4."""
    data = ministry_source()
    data.ministry_types[11] = {"id": 11, "name": "Hospitality"}
    data.ministry_type_memberships[11] = {"membership": []}
    snapshot, claim = prepare(data)
    harness.snapshot = promote(snapshot, claim, harness.campaign, harness.rings)
    configure(harness, selected=(4, 9, 11))
    harness = activate_response_service(harness)
    form = load_form(harness)
    answers = answers_for(form)
    answers["ministries"]["members"]["3"] = {"join": [9, 11], "leave": [4]}
    respond(harness, form, answers)
    return harness


def test_totals_cover_every_page_and_follow_the_filters(
    response_service, google, monkeypatch
):
    """With one request per page, the Stage counts still count every request
    the other filters select, and they follow the filters (#1133, #1684):
    Request Leave counts Choir's leave alone, a search for "Hospitality" its
    join. A request accepted moves from To review to To enter by hand."""
    harness = three_ministries(response_service)
    # A one-row page, so three requests span three pages.
    monkeypatch.setattr(ministry_followup, "PAGE_SIZES", (1, *PAGE_SIZES))
    monkeypatch.setattr(ministry_worklist, "PAGE_SIZES", (1, *PAGE_SIZES))
    browser, head = signed_in_as(harness, google, "staff@example.org", ["staff"])
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        for values, expected in (
            ({"size": "1"}, "3"),
            ({"size": "1", "page": "2"}, "3"),
            ({"size": "1", "kind": "leave"}, "1"),
            ({"size": "1", "search": "Hospitality"}, "1"),
        ):
            response, body = search(browser, PAGE, values)
            assert response.status_code == 200, values
            body = body.decode()
            assert len(listed(body)) == 1, values
            assert stages(body)[0] == f"To review ({expected})", values
    # Accept the Hospitality join: To review loses it, To enter by hand
    # has it.
    hospitality = MinistryRequest.objects.exclude(
        state__in=["superseded", "cancelled"]
    ).get(ministry_duid=11)
    edit(harness, head, hospitality, state="resolved", outcome="joined")
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response, body = search(browser, PAGE, {"size": "1"})
        assert stages(body.decode())[:2] == ["To review (2)", "To enter by hand (1)"]


def test_members_no_longer_in_parishsoft_stay_out_of_the_totals(
    response_service, google
):
    """Requests for a Member ParishSoft no longer lists as current (#1217)
    count nowhere on the merged page (#1133): once Member 3 is inactive,
    Staff's Stage counts and By Ministry counts all read zero, and only the
    Members no longer in ParishSoft view lists the requests."""
    harness = setup(response_service)
    browser, _ = signed_in_as(harness, google, "staff@example.org", ["staff"])
    join, leave = requests()
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        body = page(browser, PAGE)
        assert stages(body)[0] == "To review (2)"
        assert href(JOIN_9) in by_ministry(body)
    member_gone(harness, "inactive")
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        body = page(browser, PAGE)
        assert stages(body)[-1] == "Every stage (0)"
        section = by_ministry(body)
        assert "<a " not in section
        # Staff's table: every count cell of both Ministries is zero.
        cells = re.findall(r'<td class="numeric">([^<]*)</td>', section)
        assert len(cells) == 6 and set(cells) == {"0"}
        assert listed(body) == set()
        _, gone = search(browser, PAGE, {"members": "gone"})
        assert listed(gone.decode()) == {join.pk, leave.pk}


def join_cell(section):
    """By Ministry's Join requests cell for Food pantry (9): the linked
    count's text, or the plain zero."""
    row = section.split(">Food pantry", 1)[1].split("</tr>", 1)[0]
    return re.sub(r"<[^>]+>", "", re.findall(r'<td class="numeric">(.*?)</td>', row)[0])


def test_staffs_join_count_equals_the_list_with_its_roster_rows(
    response_service, google
):
    """A Join count opens Every stage for its Ministry, which for Staff also
    lists roster rows: an accepted join whose Member ParishSoft no longer
    lists still needs entering by hand (#1217). The count adds them, so it
    equals the list it opens and that list's Stage count (#2331)."""
    harness = setup(response_service)
    browser, head = signed_in_as(harness, google, "staff@example.org", ["staff"])
    join, _leave = requests()  # A join for Ministry 9, a leave for Ministry 4.
    edit(harness, head, join, state="resolved", outcome="joined")
    member_gone(harness, "inactive")
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        section = by_ministry(page(browser, PAGE))
        assert href(JOIN_9) in section and join_cell(section).strip() == "1"
        queue = page(browser, JOIN_9)
        assert listed(queue) == {join.pk}
        assert stages(queue)[-1] == "Every stage (1)"
        # A filtered view reads the counts the same (one more read).
        _, filtered = search(browser, PAGE, {"search": "nobody"})
        assert join_cell(by_ministry(filtered.decode())).strip() == "1"


def test_a_filtered_staff_view_costs_one_more_follow_up_read(
    response_service, google, monkeypatch
):
    """A filtered Staff view reads the default view once more for its roster
    counts (#2331): exactly EXTRA statements, whatever the filter keeps,
    never one per row. The plain view reuses its own read."""
    harness = setup(response_service)
    browser, _ = signed_in_as(harness, google, "staff@example.org", ["staff"])
    requests()  # A join for Ministry 9, a leave for Ministry 4.
    real = ministry_followup_views.read_rows
    reads = []

    def counted(campaign_id, query, principal, **options):
        """The page's read, with the statements each call runs recorded."""
        with CaptureQueriesContext(connection) as queries:
            result = real(campaign_id, query, principal, **options)
        reads.append((query, len(queries.captured_queries)))
        return result

    monkeypatch.setattr(ministry_followup_views, "read_rows", counted)

    def extra_reads(values):
        """The statements of each read beyond the worklist's own, for one
        in-place Apply of ``values``."""
        reads.clear()
        response, _body = search(browser, PAGE, values)
        assert response.status_code == 200, values
        return [count for _query, count in reads[1:]]

    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        assert extra_reads({}) == []
        assert extra_reads({"search": "nobody"}) == [EXTRA]
        assert extra_reads({"kind": "join"}) == [EXTRA]
        # The one extra read is the default view's.
        assert reads[1][0] == ministry_worklist.WorklistQuery()
