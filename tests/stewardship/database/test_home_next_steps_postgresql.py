"""Home's Next steps, signed in for real (ADM-12.21, NAV-18).

Home is the Administrator's (#1726): Staff and Ministry leaders sign in to
My review work instead, which ``test_review_work_postgresql`` and
``test_my_ministries_postgresql`` cover. The steps follow the viewer's own
menu: each is a link only while the menu offers its page and otherwise names
the menu's reason. Since #1673 each step is a link card and the 24-hour
response count is a Responses tile, riding on Home's Family participation
statement; the test email's revision is read only on a Testing draft.
"""

import re
from datetime import timedelta
from uuid import uuid4

import pytest

from parishkit.stewardship.accounts.admin_dashboard import summary
from parishkit.stewardship.accounts.content_models import ContentVersion
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.accounts.sessions import database_now
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.responses.models import Submission

from ..content_factory import content
from .auth_builders import signed_in
from .campaign_builders import add_draft, change
from .test_background_grants_postgresql import task_login
from .test_ministry_exports_postgresql import leader
from .test_ministry_reports_postgresql import actor, setup
from .test_report_workspace_postgresql import read

pytestmark = pytest.mark.django_db(transaction=True)


def panel(body):
    """The Next steps panel's HTML, or None when Home shows none."""
    found = re.search(rb"<section[^>]*data-next-steps.*?</section>", body, re.S)
    return found[0].decode() if found else None


def steps(html):
    """Each step card's text, tags stripped and spaces collapsed, in order."""
    return [
        " ".join(re.sub(r"<[^>]+>", " ", item).split())
        for item in re.findall(r"<li [^>]*data-link-card>(.*?)</li>", html, re.S)
    ]


def recent_tile(body):
    """The Responses panel's past-24-hours value (#1673), or None."""
    found = re.search(
        rb'data-stat="recent"><span class="stat-label">[^<]*</span> '
        rb'<span class="stat-value">([^<]*)</span>',
        body,
    )
    return found[1].decode() if found else None


def test_testing_draft_offers_the_setup_next_steps(auth_service, google):
    """A Testing draft's Next steps, each linking the Administrator's menu
    entry; the test email opens the initial invitation."""
    store = auth_service.store
    add_draft(store, store.active(), uuid4())
    # The initial invitation's email, which Preview and test email opens.
    owner = str(Campaign.objects.get().pk)
    invitation = content(owner, kind="email", slot="initial")
    patch = [{"operation": "add", "section": "content", **invitation}]
    assert change(store, store.active(), uuid4(), patch).state == "applied"
    browser, response = signed_in()
    assert response.status_code == 302
    body = browser.get("/admin/").content
    html = panel(body)
    assert steps(html) == [
        # The Campaign setup stepper's way back in (#1404): the draft has no
        # Families yet, so Load ParishSoft data, step 2 (PR 5), is the first
        # step not done.
        "Continue campaign setup: Load ParishSoft data",
        "Campaign settings",
        "Pages and emails",
        "Dates and mail schedules",
        "Preview and test email",
        "Go live",
    ]
    assert ContentVersion.objects.filter(record_id=invitation["id"]).exists()
    assert f'href="/admin/campaign/content/test/{invitation["id"]}/"' in html
    assert 'href="/admin/campaign/settings/"' in html
    # The response count is a tile, not a Today line (#1673).
    assert "Families responded" not in html and "Today:" not in html
    assert recent_tile(body) == "0"
    # The panel comes after every other panel on Home.
    assert b"<section" not in body[body.index(b"data-next-steps") :]


def test_no_current_campaign_shows_no_next_steps(auth_service, google):
    """With no campaign, Home's campaign panel says what to do instead."""
    browser, response = signed_in()
    assert response.status_code == 302
    body = browser.get("/admin/").content
    assert panel(body) is None
    assert b"No campaign yet" in body or b"no current campaign" in body


def test_active_campaign_steps_and_counts(response_service, google):
    """On an active campaign, an Administrator's step cards carry the open
    counts and a Responses tile counts the Families who responded in the
    past 24 hours."""
    harness = setup(response_service)
    browser, _, _, _ = leader(harness, google, roles=("administrator",))
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response, body = read(browser, "/admin/")
    assert response.status_code == 200
    html = panel(body)
    listed = steps(html)
    assert [re.sub(r" \d.*", "", step) for step in listed] == [
        "Census changes",
        "Ministry requests to enter by hand",
        "Additional information",
        "Ministry requests",
        "Family email progress",
        "Sent mail",
    ]
    # No request is resolved yet, so nothing waits to be entered (#1590).
    assert "Ministry requests to enter by hand 0 to enter by hand" in listed
    # The follow-up request the harness made is open in the queue.
    assert "Ministry requests 2 open" in listed
    assert 'href="/admin/mail/sent/"' in html
    since = database_now() - timedelta(hours=24)
    recent = (
        Submission.objects.filter(
            campaign=harness.campaign, mode="live", submitted_at__gte=since
        )
        .values("family")
        .distinct()
        .count()
    )
    assert recent >= 1
    assert recent_tile(body) == str(recent)
    assert "Today:" not in html


def test_status_read_leaves_the_home_only_values_out(response_service):
    """The command line's status read computes neither the 24-hour count nor
    the test email."""
    harness = setup(response_service)
    principal = actor(harness, ("administrator",), ())
    configuration = SystemConfiguration.objects.select_related(
        "active_configuration"
    ).get()
    configuration.current_campaign = harness.campaign
    now = database_now()
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        status = summary(principal, configuration, now)
        home = summary(principal, configuration, now, home=True)
    assert "recent" not in status["families"] and "recent" in home["families"]
    # An active campaign admits no test email from Home.
    assert "test_email" not in home


def test_active_families_with_a_response_leave_out_an_inactive_family(
    response_service,
):
    """Home's "Active Families with a response" tile (#1673) counts only
    active Families: a Family made inactive (and so no longer eligible, as
    the table's constraint requires) keeps its response in the all-Families
    count but leaves this one."""
    from django.db.models import F

    from parishkit.stewardship.campaigns.credential_models import FamilyCampaign

    harness = setup(response_service)
    principal = actor(harness, ("administrator",), ())
    configuration = SystemConfiguration.objects.select_related(
        "active_configuration"
    ).get()
    configuration.current_campaign = harness.campaign
    rows = FamilyCampaign.objects.filter(campaign=harness.campaign)
    answered = rows.filter(active=True, first_live_submission_id__isnull=False)
    before = answered.count()
    assert before >= 1
    rows.filter(pk=answered.values("pk")[:1]).update(
        active=False, portal_eligible=False, version=F("version") + 1
    )
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        home = summary(principal, configuration, database_now(), home=True)
    families = home["families"]
    assert families["active_responded"] == before - 1
    assert (
        families["responded"]
        == rows.filter(first_live_submission_id__isnull=False).count()
    )
    assert families["responded"] == families["active_responded"] + 1
    tile = next(t for t in home["family_tiles"] if t.key == "responded")
    assert tile.text == str(before - 1)
