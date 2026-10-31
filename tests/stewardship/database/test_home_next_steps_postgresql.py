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
        "Mail queue",
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


def test_campaign_families_with_a_response_leave_out_a_family_that_left(
    response_service,
):
    """Home's "Campaign Families responded" tile (#1673) counts only
    campaign Families: a Family that left the campaign (no longer eligible,
    and here inactive too) keeps its response in the all-Families count but
    leaves this one."""
    from django.db.models import F

    from parishkit.stewardship.campaigns.credential_models import FamilyCampaign

    harness = setup(response_service)
    principal = actor(harness, ("administrator",), ())
    configuration = SystemConfiguration.objects.select_related(
        "active_configuration"
    ).get()
    configuration.current_campaign = harness.campaign
    rows = FamilyCampaign.objects.filter(campaign=harness.campaign)
    answered = rows.filter(portal_eligible=True, first_live_submission_id__isnull=False)
    before = answered.count()
    assert before >= 1
    rows.filter(pk=answered.values("pk")[:1]).update(
        active=False, portal_eligible=False, version=F("version") + 1
    )
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        home = summary(principal, configuration, database_now(), home=True)
    families = home["families"]
    assert families["eligible_responded"] == before - 1
    assert (
        families["responded"]
        == rows.filter(first_live_submission_id__isnull=False).count()
    )
    assert families["responded"] == families["eligible_responded"] + 1
    tile = next(t for t in home["family_tiles"] if t.key == "responded")
    assert tile.text == str(before - 1)


def test_home_and_go_live_count_the_rules_families_as_the_dashboard_does(
    live_response_service,
):
    """Home's Campaign Families tiles and Go live's Family counts are the
    Families the campaign's rule selects (#2331): a Family active in
    ParishSoft but registered elsewhere is a campaign row (``active``) the
    rule does not choose, so neither counts it, and both equal the Response
    dashboard's Families and the directory's default count."""
    from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
    from parishkit.stewardship.reports.statistics import calculate_statistics
    from parishkit.stewardship.reports.statistics_selection import (
        capture_statistics,
    )

    from .response_builders import response_source
    from .test_directories_postgresql import page
    from .test_source_families_postgresql import prepare, promote
    from .test_weekly_observation_postgresql import respond

    harness = live_response_service
    data = response_source()
    # Family 90 is active in ParishSoft but registered elsewhere, so the
    # campaign's (legacy) rule does not select it.
    data.families[90] = data.families[1] | {
        "familyDUID": 90,
        "familyID": 190,
        "registeredOrganizationID": 999,
    }
    data.members[1090] = data.members[3] | {"memberDUID": 1090, "familyDUID": 90}
    snapshot, claim = prepare(data)
    promote(snapshot, claim, harness.campaign, harness.rings)
    rows = FamilyCampaign.objects.filter(campaign=harness.campaign)
    outside = rows.get(family_duid=90)
    assert_mail_plan_leaves_out(rows, outside, harness.campaign.pk)
    respond(harness, "Thank you.")
    assert outside.active and not outside.portal_eligible
    assert (
        rows.filter(active=True).count()
        == rows.filter(portal_eligible=True).count() + 1
    )

    principal = actor(harness, ("administrator",), ())
    configuration = SystemConfiguration.objects.select_related(
        "active_configuration"
    ).get()
    configuration.current_campaign = harness.campaign
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        home = summary(principal, configuration, database_now(), home=True)
        dashboard = calculate_statistics(capture_statistics(harness.campaign.pk))
    families = home["families"]
    tiles = {tile.key: tile for tile in home["family_tiles"]}
    assert families["active"] == dashboard.active.families
    assert families["eligible"] == dashboard.active.families == 1
    assert page(harness)["active_total"] == dashboard.active.families
    assert tiles["active"].text == str(dashboard.active.families)
    assert families["eligible_responded"] == dashboard.active.responses == 1
    assert tiles["responded"].text == str(dashboard.active.responses)
    assert (
        tiles["responded"]
        .details[0]
        .startswith(f"out of {dashboard.active.families} (")
    )

    # Go live's Check, Confirm Production and ``pk-admin go-live`` counts.
    impact = go_live_impact(harness.campaign.pk)
    assert impact.families == rows.count()
    assert impact.active == dashboard.active.families
    assert impact.email_eligible == dashboard.active.eligible_email
    assert impact.no_eligible_email == (
        dashboard.active.families - dashboard.active.eligible_email
    )


def go_live_impact(campaign_pk):
    """Return the go-live preview's Family impact counts for one campaign."""
    from parishkit.stewardship.campaigns.readiness_families import family_impact
    from parishkit.stewardship.campaigns.work_locks import work_transaction

    campaign = Campaign.objects.select_related("active_configuration").get(
        pk=campaign_pk
    )
    with task_login(ServiceRole.WEB), work_transaction():
        return family_impact(
            campaign, cutoff=campaign.active_configuration.starts_at
        ).counts


def assert_mail_plan_leaves_out(rows, outside, campaign_pk):
    """Check a ParishSoft-active Family the rule does not choose changes no mail.

    The mail plan is unchanged by #2331: the planner sends to rows that are
    ``active`` with an eligible email, the go-live preview to
    ``portal_eligible`` ones with an eligible email. They are the same
    Families, since email eligibility already needs the rule, and
    ``outside`` is in neither. Taking ``outside`` out of ParishSoft's active
    Families altogether leaves the preview's messages, recipients and slots
    exactly as they were; the row is then restored.
    """
    from django.db.models import F

    planned = set(rows.filter(active=True, email_eligible=True).values_list("pk"))
    assert planned == set(
        rows.filter(portal_eligible=True, email_eligible=True).values_list("pk")
    )
    assert (outside.pk,) not in planned
    names = (
        "email_eligible",
        "deliverable",
        "messages",
        "coalesced_slots",
        "skipped_slots",
        "blocked_families",
    )
    before = go_live_impact(campaign_pk)
    assert before.deliverable >= 1
    for active in (False, True):
        rows.filter(pk=outside.pk).update(active=active, version=F("version") + 1)
        after = go_live_impact(campaign_pk)
        assert {n: getattr(after, n) for n in names} == {
            n: getattr(before, n) for n in names
        }
    assert after.active == before.active
