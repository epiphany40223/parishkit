"""Review worklists mark the Families no longer in this campaign (#2331).

A review worklist lists every Family's answers, Families that left the
campaign included, since those still need a decision; the report pages
count only the Families in this campaign today by default. So each
Family-named worklist marks such a Family with the shared "No longer in this
campaign" badge beside its name, and its About says why it can count more.
Additional information stands in for the three Family-named worklists that
share the component and ``link_families``; Pledges reads its selection's own
in-campaign flag, so it has its own test.
"""

import re

import pytest
from django.db.models import F
from django.urls import reverse

from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.source.models import SourceCurrent

from .auth_builders import signed_in
from .test_background_grants_postgresql import task_login
from .test_financial_report_postgresql import _rewrite_payload
from .test_pledge_review_postgresql import pledged, staff_browser
from .test_report_workspace_postgresql import read as get
from .test_weekly_observation_postgresql import respond

pytestmark = pytest.mark.django_db(transaction=True)

PAGE = "/admin/reports/information/"
BADGE = '<span class="status-badge" data-source-status="left">'
ABOUT = "The list includes Families no longer in this campaign"


def table(body):
    """The worklist table's body rows."""
    return body.split("<tbody>", 1)[1].split("</tbody>", 1)[0]


def test_a_family_that_left_keeps_its_row_with_the_badge(live_response_service, google):
    """The request stays listed and counted at To review after its Family
    leaves the campaign, now with the badge after its name; the About says
    the list includes such Families."""
    harness = live_response_service
    submission = respond(harness, "Please call us")
    browser, login = signed_in()
    assert login.status_code == 302
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response, body = get(browser, PAGE)
        assert response.status_code == 200
        body = body.decode()
        assert ABOUT in body and BADGE not in table(body)
        assert re.search(r">To review \(1\)<", body)
    FamilyCampaign.objects.filter(pk=submission.family_id).update(
        portal_eligible=False, version=F("version") + 1
    )
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response, body = get(browser, PAGE)
        assert response.status_code == 200
        body = body.decode()
        rows = table(body)
        assert rows.count(BADGE) == 1 and "No longer in this campaign" in rows
        assert re.search(r">To review \(1\)<", body)


def test_pledges_marks_a_family_that_left_from_its_own_flag(response_service, google):
    """Pledges reads the in-campaign flag from its selection (the snapshot's
    portal_eligible), not ``link_families``: once the Family no longer
    matches the campaign's Families rule, its pledge stays listed and
    counted at To review, with the badge after its name."""
    harness, submission = pledged(response_service)
    route = reverse("admin:pledges_to_enter")
    browser, _staff = staff_browser(harness, google)
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response, body = get(browser, route)
        assert response.status_code == 200
        body = body.decode()
        assert ABOUT in body and BADGE not in table(body)
    duid = FamilyCampaign.objects.get(pk=submission.family_id).family_duid
    snapshot = SourceCurrent.objects.get().snapshot_id
    _rewrite_payload("family", snapshot, duid, {"portal_eligible": False})
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response, body = get(browser, route)
        assert response.status_code == 200
        body = body.decode()
        rows = table(body)
        assert rows.count(BADGE) == 1 and "No longer in this campaign" in rows
        assert re.search(r">To review \(1\)<", body)
