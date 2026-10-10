"""Home's My Ministries panel for Ministry leaders (#533 slice 1).

The panel reads the follow-up queue's own selection with the leader's scope,
so it counts exactly the open requests the queue would list for them; each
Ministry's link opens that queue filtered to it by GET, checked against the
person's scope, and the request pages keep the filter on the way back.
"""

import re
from uuid import uuid4

import pytest
from django.db import transaction

from parishkit.stewardship.accounts.admin_dashboard import leads_ministries
from parishkit.stewardship.accounts.policy import Principal
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.reports.ministry_followup import my_ministries

from .test_background_grants_postgresql import task_login
from .test_live_ministries_postgresql import applied, select
from .test_ministry_exports_postgresql import leader
from .test_ministry_followup_postgresql import requests
from .test_ministry_reports_postgresql import actor, setup
from .test_report_workspace_postgresql import read as get

pytestmark = pytest.mark.django_db(transaction=True)


def summary(harness, principal):
    """The panel's data through the web login's real grants."""
    with task_login(ServiceRole.WEB, exact=True, reconnect=True), transaction.atomic():
        return my_ministries(harness.campaign.pk, principal)


def counts(mine):
    """(DUID, in campaign, joining, leaving) per listed Ministry."""
    return [
        (m["duid"], m["in_campaign"], m["join"], m["leave"]) for m in mine["ministries"]
    ]


def test_counts_are_exact_and_follow_the_leaders_scope(response_service):
    """A leader counts only its own Ministries' open requests, exactly."""
    harness = setup(response_service)
    requests()  # A join for Ministry 9, a leave for Ministry 4.
    scoped = actor(harness, ("ministry_leader",), (9,))
    mine = summary(harness, scoped)
    assert counts(mine) == [(9, True, 1, 0)] and mine["ministries"][0]["name"]
    both = actor(harness, ("ministry_leader",), (4, 9))
    assert sorted(counts(summary(harness, both))) == [(4, True, 0, 1), (9, True, 1, 0)]
    # Someone with no Ministry in scope gets no panel at all.
    nobody = actor(harness, ("ministry_leader",), ())
    assert summary(harness, nobody) is None


def test_a_removed_ministry_leaves_its_leaders_panel(response_service):
    """A Ministry removed from the campaign is no longer its leaders' (#922):
    they lead it only while the campaign asks about it, so the panel is gone.
    Administrators and Staff keep its open requests."""
    harness = setup(response_service)
    requests()
    scoped = actor(harness, ("ministry_leader",), (9,))
    assert counts(summary(harness, scoped)) == [(9, True, 1, 0)]
    assert applied(select(harness, [4]))  # Ministry 9 leaves the campaign.
    assert summary(harness, scoped) is None


def test_home_links_the_queue_by_get_and_the_filter_survives(response_service, google):
    """The leader's Home links each Ministry's queue; Back, reload and the
    request page keep the Ministry; a number outside the scope is refused."""
    harness = setup(response_service)
    browser, _, _, _ = leader(harness, google)
    join, leave = requests()
    route = "/admin/reports/ministries/follow-up/"
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response, body = get(browser, "/admin/")
        assert response.status_code == 200
        panel = re.search(rb"<section[^>]*data-my-ministries.*?</section>", body, re.S)
        assert panel, "the My Ministries panel is shown"
        panel = panel[0]
        assert b'data-ministry="9"' in panel and b'data-ministry="4"' not in panel
        assert b"1 asking to join" in panel
        link = f'href="{route}?ministry=9"'.encode()
        assert link in panel
        # The link is an ordinary GET: reloading it is the same request.
        for _ in range(2):
            response, body = get(browser, route + "?ministry=9")
            assert response.status_code == 200
            assert str(join.pk).encode() in body and str(leave.pk).encode() not in body
        # The view is remembered like any other (#534): the request page and
        # its "Return to Ministry follow-up" carry its token, which keeps the
        # Ministry.
        token = re.search(rf"{route}{join.pk}/\?queue=([A-Za-z0-9_-]+)".encode(), body)
        assert token, "the request link carries the remembered view"
        token = token[1].decode()
        response, detail = get(browser, f"{route}{join.pk}/?queue={token}")
        assert response.status_code == 200
        assert f'href="{route}?queue={token}"'.encode() in detail
        response, back = get(browser, f"{route}?queue={token}")
        assert response.status_code == 200
        assert str(join.pk).encode() in back and str(leave.pk).encode() not in back
        # Only the Ministry may travel in a URL, and only one in scope.
        assert get(browser, route + "?ministry=4")[0].status_code == 403
        assert get(browser, route + "?ministry=09")[0].status_code == 400
        assert get(browser, route + "?ministry=9&search=x")[0].status_code == 400
        assert get(browser, f"{route}?ministry=9&queue={token}")[0].status_code == 400
        assert get(browser, f"{route}{join.pk}/?ministry=9")[0].status_code == 400
        # A refused link says so plainly: no form was sent, and reloading the
        # same address would only repeat the error.
        for address in (route + "?ministry=09", f"{route}{join.pk}/?ministry=9"):
            response, body = get(browser, address)
            assert response.status_code == 400
            assert b"This link isn't valid any more." in body
            assert b"could not be read" not in body and b"Reload" not in body
            assert f'href="{route}"'.encode() in body


def test_administrators_and_staff_get_no_panel():
    """Campaign-wide follow-up already reaches every Ministry from the menu."""
    someone = uuid4()
    for roles in ({"administrator", "staff", "ministry_leader"}, {"staff"}):
        principal = Principal(someone, frozenset(roles), frozenset({4, 9}))
        assert not leads_ministries(principal)
    assert leads_ministries(
        Principal(someone, frozenset({"ministry_leader"}), frozenset({9}))
    )
    assert not leads_ministries(
        Principal(someone, frozenset({"ministry_leader"}), frozenset())
    )
