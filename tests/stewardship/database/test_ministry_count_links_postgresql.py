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

from .test_background_grants_postgresql import task_login
from .test_ministry_exports_postgresql import leader
from .test_ministry_followup_postgresql import edit, requests
from .test_ministry_reports_postgresql import setup
from .test_report_workspace_postgresql import read as get

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
