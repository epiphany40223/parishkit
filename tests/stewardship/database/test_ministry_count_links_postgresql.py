"""Ministry requests' counts open the filtered follow-up queue (#1133 step 1).

Each count links to the queue with its Ministry and the queue's own closed
Request and Status values, so the queue opened by the link lists exactly the
requests counted; only Ministries the reader may follow up get links, and a
zero count is plain text.
"""

import re
from uuid import UUID

import pytest

from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.reports import ministries
from parishkit.stewardship.web.tables import PAGE_SIZES

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

REPORT = "/admin/reports/ministries/"
QUEUE = "/admin/reports/ministries/follow-up/"
JOIN_9 = f"{QUEUE}?ministry=9&action=join&state=any"
LEAVE_4 = f"{QUEUE}?ministry=4&action=leave&state=any"


def page(browser, path):
    """One page's body as text, after checking it was shown."""
    response, body = get(browser, path)
    assert response.status_code == 200, path
    return body.decode()


def listed(body):
    """The request ids the queue page lists, from each row's request link."""
    return {UUID(pk) for pk in re.findall(rf"{QUEUE}([0-9a-f-]{{36}})/\?queue=", body)}


def href(address):
    """An address as it appears, escaped, in an href."""
    return f'href="{address.replace("&", "&amp;")}"'


def selected(body, select_id):
    """The selected option's value in one of the queue's filter controls."""
    control = body.split(f'id="{select_id}"', 1)[1].split("</select>", 1)[0]
    match = re.search(r'<option value="([^"]*)" selected>', control)
    return match[1] if match else None


def test_a_leaders_counts_open_exactly_the_counted_requests(response_service, google):
    """A leader of Food pantry (9) gets links for it alone; each opens the
    queue with its filters shown and lists exactly the requests counted,
    resolved requests included in the join count; a zero count is text."""
    harness = setup(response_service)
    browser, head, _, _ = leader(harness, google)
    join, leave = requests()  # A join for Ministry 9, a leave for Ministry 4.
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        body = page(browser, REPORT)
        assert href(JOIN_9) in body and href(f"{QUEUE}?ministry=9") in body
        # Every link's accessible name names its Ministry.
        assert 'aria-label="1 join requests for Food pantry"' in body
        assert 'aria-label="1 unresolved requests for Food pantry"' in body
        # The retired lists' Contact details controls are gone (#1157).
        assert "Contact details of Members" not in body
        # Food pantry has no leave request: the zero is plain text, and
        # Ministry 4 is not this leader's.
        assert "0 leave requests</td>" in body
        assert "action=leave" not in body and "ministry=4" not in body
        queue = page(browser, JOIN_9)
        assert listed(queue) == {join.pk}
        assert selected(queue, "filter-ministry") == "9"
        assert selected(queue, "filter-action") == "join"
        assert selected(queue, "filter-state") == "any"
        assert listed(page(browser, f"{QUEUE}?ministry=9")) == {join.pk}

    # Resolved, the request still counts as joining but no longer unresolved.
    edit(harness, head, join, state="resolved", outcome="joined")
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        body = page(browser, REPORT)
        assert href(JOIN_9) in body and href(f"{QUEUE}?ministry=9") not in body
        assert 'aria-label="0 unresolved requests' not in body
        assert listed(page(browser, JOIN_9)) == {join.pk}
        # Only closed filter values with a Ministry in scope may travel.
        for refused, status in (
            (LEAVE_4, 403),
            (f"{QUEUE}?action=join", 400),
            (f"{QUEUE}?ministry=9&action=maybe", 400),
            (f"{QUEUE}?ministry=9&state=any&search=x", 400),
            (f"{QUEUE}?ministry=9&action=join&queue=abc", 400),
        ):
            assert get(browser, refused)[0].status_code == status, refused
    assert leave.pk not in listed(queue)


def test_staff_counts_link_every_ministry(response_service, google):
    """Staff follow up every Ministry, so each count that is not zero links."""
    harness = setup(response_service)
    browser, _, _, _ = leader(harness, google, roles=("staff",))
    join, leave = requests()
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        body = page(browser, REPORT)
        assert href(JOIN_9) in body and href(LEAVE_4) in body
        assert href(f"{QUEUE}?ministry=4") in body
        assert listed(page(browser, LEAVE_4)) == {leave.pk}
        assert listed(page(browser, f"{QUEUE}?ministry=4")) == {leave.pk}
        assert join.pk not in listed(page(browser, LEAVE_4))


def totals(body):
    """The totals region's tile values and follow-up chart key (#720)."""
    region = body.split('<div id="ministry-totals" data-table-sync>', 1)[1]
    region = region.split("<div id=", 1)[0]
    values = re.findall(r'<span class="stat-value">([^<]*)</span>', region)
    return values, re.findall(r'data-chart="([a-z-]+)"', region)


@pytest.mark.parametrize(
    "roles, expected", [(None, ["1", "0"]), (("staff",), ["1", "1"])]
)
def test_totals_add_up_only_the_readers_ministries(
    response_service, google, roles, expected
):
    """The totals and follow-up chart come from the same scoped summary as
    the table (#720): a leader of Food pantry (9) counts its join request
    alone, while Staff count Ministry 4's leave request too."""
    harness = setup(response_service)
    browser, _, _, _ = leader(harness, google, **({"roles": roles} if roles else {}))
    requests()  # A join for Ministry 9, a leave for Ministry 4.
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        assert totals(page(browser, REPORT)) == (expected, ["ministry-followup-spec"])


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


def test_totals_cover_every_page_and_follow_the_search(
    response_service, google, monkeypatch
):
    """With one Ministry per page, the totals still add up every matching
    Ministry (#720): the view's second, whole-summary read goes through the
    real scoped selection with the search, so "i" (Choir and Hospitality,
    not Food pantry) counts Hospitality's join and Choir's leave only."""
    harness = three_ministries(response_service)
    # A one-row page, so three Ministries span three pages.
    monkeypatch.setattr(ministries, "PAGE_SIZES", (1, *PAGE_SIZES))
    browser, _, _, _ = leader(harness, google, roles=("staff",))
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        for values, expected, shown in (
            ({"size": "1"}, ["2", "1"], "Choir"),
            ({"size": "1", "page": "2"}, ["2", "1"], "Food pantry"),
            ({"size": "1", "search": "i"}, ["1", "1"], "Choir"),
            ({"size": "1", "search": "i", "page": "2"}, ["1", "1"], "Hospitality"),
        ):
            response, body = search(browser, REPORT, values)
            assert response.status_code == 200, values
            body = body.decode()
            table = body.split("Current Ministry request summary", 1)[1]
            table = table.split("</table>", 1)[0]
            assert table.count('<th scope="row">') == 1, values
            assert shown in table, values
            assert totals(body) == (expected, ["ministry-followup-spec"]), values
