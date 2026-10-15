"""Ministry requests (#1133): the follow-up queue with its totals and By
Ministry counts, under the real web role and SQL scope.

Each By Ministry count links to the same page with its Ministry and the
queue's own closed Request and Status values, so the view it opens lists
exactly the requests counted; only Ministries the reader may follow up get
links, and a zero count is plain text. A leader of one Ministry gets one
line, a leader of several and Staff and Administrators a table. The totals
count what the table's filters select, within the reader's scope.
"""

import re
from uuid import UUID, uuid4

import pytest

from parishkit.stewardship.accounts.policy_models import PortalUser
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.reports import ministry_followup
from parishkit.stewardship.web.tables import PAGE_SIZES
from parishkit.stewardship.workflows.models import MinistryRequest

from ..policy_factory import address
from .auth_builders import signed_in
from .campaign_builders import change
from .response_builders import activate_response_service
from .test_background_grants_postgresql import task_login
from .test_information_followup_postgresql import search
from .test_ministry_exports_postgresql import leader
from .test_ministry_followup_postgresql import edit, requests
from .test_ministry_reports_postgresql import prepare, setup
from .test_ministry_responses_postgresql import configure, ministry_source, respond
from .test_report_workspace_postgresql import read as get
from .test_response_http_postgresql import answers_for, load_form
from .test_source_families_postgresql import promote

pytestmark = pytest.mark.django_db(transaction=True)

PAGE = "/admin/reports/ministries/"
ITEMS = "/admin/reports/ministries/follow-up/"
JOIN_9 = f"{PAGE}?ministry=9&action=join&state=any"
LEAVE_4 = f"{PAGE}?ministry=4&action=leave&state=any"


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


def totals(body):
    """The totals region's tile values and follow-up chart key (#720)."""
    region = body.split('<div id="ministry-totals" data-table-sync>', 1)[1]
    region = region.split('<section id="by-ministry"', 1)[0]
    values = re.findall(r'<span class="stat-value">([^<]*)</span>', region)
    return values, re.findall(r'data-chart="([a-z-]+)"', region)


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


def test_a_leader_of_one_ministry_gets_one_line_whose_counts_filter_the_table(
    response_service, google
):
    """A leader of Food pantry (9) gets one line, linked for it alone; each
    count opens the table with its filters shown and lists exactly the
    requests counted, resolved requests included in the join count; a zero
    count is text, and only closed filter values in scope may travel."""
    harness = setup(response_service)
    browser, head, _, _ = leader(harness, google)
    join, leave = requests()  # A join for Ministry 9, a leave for Ministry 4.
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        body = page(browser, PAGE)
        section = by_ministry(body)
        assert "<table" not in section and 'class="by-ministry-line"' in section
        assert href(JOIN_9) in section and href(f"{PAGE}?ministry=9") in section
        # Every link's accessible name names its Ministry.
        assert 'aria-label="1 join request for Food pantry"' in section
        assert 'aria-label="1 unresolved request for Food pantry"' in section
        # Food pantry has no leave request: the zero is plain text, and
        # Ministry 4 is not this leader's.
        assert "0 leave requests" in section and "action=leave" not in section
        assert "ministry=4" not in body
        # The default view lists the open join, and the leader's packet
        # offers Food pantry alone.
        assert listed(body) == {join.pk}
        packet = body.split('id="ministry-packet-choices"', 1)[1].split("</fieldset>")[
            0
        ]
        assert packet.count('name="ministries"') == 1 and 'value="9"' in packet
        queue = page(browser, JOIN_9)
        assert listed(queue) == {join.pk}
        assert selected(queue, "filter-ministry") == "9"
        assert selected(queue, "filter-action") == "join"
        assert selected(queue, "filter-state") == "any"
        assert listed(page(browser, f"{PAGE}?ministry=9")) == {join.pk}

    # Resolved, the request still counts as joining but no longer unresolved.
    edit(harness, head, join, state="resolved", outcome="joined")
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        section = by_ministry(page(browser, PAGE))
        assert href(JOIN_9) in section and href(f"{PAGE}?ministry=9") not in section
        assert listed(page(browser, JOIN_9)) == {join.pk}
        for refused, status in (
            (LEAVE_4, 403),
            (f"{PAGE}?action=join", 400),
            (f"{PAGE}?ministry=9&action=maybe", 400),
            (f"{PAGE}?ministry=9&state=any&search=x", 400),
            (f"{PAGE}?ministry=9&action=join&queue=abc", 400),
        ):
            assert get(browser, refused)[0].status_code == status, refused
    assert leave.pk not in listed(queue)


def test_a_leader_of_several_ministries_gets_a_table_of_them(response_service, google):
    """A leader of Choir (4) and Food pantry (9) gets a By Ministry table
    with a row and links for each, and no Admin/Staff marks (#1135)."""
    harness = setup(response_service)
    browser, _ = signed_in_as(
        harness, google, "leader-4-9@example.org", ["ministry_leader"]
    )
    join, leave = requests()
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        body = page(browser, PAGE)
        section = by_ministry(body)
        assert section.count('<th scope="row">') == 2
        assert href(JOIN_9) in section and href(LEAVE_4) in section
        assert "Not in ParishSoft data" not in section
        assert listed(body) == {join.pk, leave.pk}
        assert listed(page(browser, LEAVE_4)) == {leave.pk}
        assert totals(body) == (["1", "1"], ["ministry-followup-spec"])


@pytest.mark.parametrize("role", ["staff", "administrator"])
def test_staff_and_administrators_count_every_ministry(response_service, google, role):
    """Staff and Administrators follow up every Ministry, so each count that
    is not zero links; the old queue address is gone, with no redirect."""
    harness = setup(response_service)
    browser, _, _, _ = leader(harness, google, roles=(role,))
    join, leave = requests()
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        section = by_ministry(page(browser, PAGE))
        assert href(JOIN_9) in section and href(LEAVE_4) in section
        assert href(f"{PAGE}?ministry=4") in section
        assert listed(page(browser, LEAVE_4)) == {leave.pk}
        assert join.pk not in listed(page(browser, LEAVE_4))
        assert get(browser, ITEMS)[0].status_code == 404


@pytest.mark.parametrize(
    "roles, expected", [(None, ["1", "0"]), (("staff",), ["1", "1"])]
)
def test_totals_add_up_only_the_readers_requests(
    response_service, google, roles, expected
):
    """The totals and follow-up chart come from the queue's own scoped
    selection (#720, #1133): a leader of Food pantry (9) counts its join
    request alone, while Staff count Ministry 4's leave request too."""
    harness = setup(response_service)
    browser, _, _, _ = leader(harness, google, **({"roles": roles} if roles else {}))
    requests()  # A join for Ministry 9, a leave for Ministry 4.
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        assert totals(page(browser, PAGE)) == (expected, ["ministry-followup-spec"])


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
    """With one request per page, the totals still count every request the
    filters select (#720), and they follow the filters (#1133): Request
    Leave counts Choir's leave alone, a search for "Hospitality" its join.
    The follow-up chart counts the same requests whatever their status, so
    a resolved request leaves Unresolved but stays in the chart."""
    harness = three_ministries(response_service)
    # A one-row page, so three requests span three pages.
    monkeypatch.setattr(ministry_followup, "PAGE_SIZES", (1, *PAGE_SIZES))
    browser, head = signed_in_as(harness, google, "staff@example.org", ["staff"])
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        for values, expected in (
            ({"size": "1"}, ["2", "1"]),
            ({"size": "1", "page": "2"}, ["2", "1"]),
            ({"size": "1", "action": "leave"}, ["0", "1"]),
            ({"size": "1", "search": "Hospitality"}, ["1", "0"]),
        ):
            response, body = search(browser, PAGE, values)
            assert response.status_code == 200, values
            body = body.decode()
            assert len(listed(body)) == 1, values
            assert totals(body) == (expected, ["ministry-followup-spec"]), values
    # Resolve the Hospitality join: the Unresolved view loses it, and the
    # chart, which counts every status, still has it.
    hospitality = MinistryRequest.objects.exclude(
        state__in=["superseded", "cancelled"]
    ).get(ministry_duid=11)
    edit(harness, head, hospitality, state="resolved", outcome="joined")
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response, body = search(browser, PAGE, {"size": "1"})
        assert totals(body.decode()) == (["1", "1"], ["ministry-followup-spec"])
        assert "1 out of 3" in body.decode()
