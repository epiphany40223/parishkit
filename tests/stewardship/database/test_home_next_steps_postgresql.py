"""Home's Next steps and Today line, signed in for real (ADM-12.21, NAV-18).

The steps follow the viewer's own menu: each is a link only while the menu
offers its page and otherwise names the menu's reason. The Today count rides
on Home's Family participation statement, and the test email's revision is
read only for an Administrator on a Testing draft.
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
from ..policy_factory import address
from .auth_builders import signed_in
from .campaign_builders import add_draft, change
from .leader_builders import make_leader
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
    """Each step's text, tags stripped, in order."""
    return [
        re.sub(r"<[^>]+>", "", item)
        for item in re.findall(r"<li data-next-step>(.*?)</li>", html, re.S)
    ]


def sign_in(store, google, role):
    """Sign in as ``role`` on the current draft; return the browser."""
    if role == "ministry_leader":
        make_leader(store, Campaign.objects.get(), "reader@example.org")
        google[0]["email"] = "reader@example.org"
    elif role == "staff":
        rule = address("reader@example.org", roles=("staff",))
        change(
            store,
            store.active(),
            uuid4(),
            [{"operation": "add", "section": "login_rules", **rule}],
        )
        google[0]["email"] = "reader@example.org"
    browser, response = signed_in()
    assert response.status_code == 302
    return browser


@pytest.mark.parametrize(
    ("role", "expected"),
    [
        (
            "administrator",
            [
                # The Campaign setup stepper's way back in (#1404): adding
                # the invitation completed Pages and emails.
                "Continue campaign setup: Dates and mail schedules",
                "Campaign settings",
                "Pages and emails",
                "Dates and mail schedules",
                "Preview and test email",
                "Go-live readiness",
                "Response dashboard",
            ],
        ),
        ("staff", ["Response dashboard"]),
        # Ministry requests carries the follow-up queue's open count (#1133).
        ("ministry_leader", ["Ministry requests — 0 open"]),
    ],
)
def test_testing_draft_offers_each_roles_next_steps(
    auth_service, google, role, expected
):
    """A Testing draft's Next steps, by role, each linking the viewer's menu
    entry; the Administrator's test email opens the initial invitation."""
    store = auth_service.store
    add_draft(store, store.active(), uuid4())
    # The initial invitation's email, which Preview and test email opens.
    owner = str(Campaign.objects.get().pk)
    invitation = content(owner, kind="email", slot="initial")
    patch = [{"operation": "add", "section": "content", **invitation}]
    assert change(store, store.active(), uuid4(), patch).state == "applied"
    browser = sign_in(store, google, role)
    body = browser.get("/admin/").content
    html = panel(body)
    assert html, "Home shows Next steps on a current campaign"
    assert steps(html) == expected
    if role == "administrator":
        assert ContentVersion.objects.filter(record_id=invitation["id"]).exists()
        assert f'href="/admin/campaign/content/test/{invitation["id"]}/"' in html
        assert 'href="/admin/campaign/settings/"' in html
    # Only those who read campaign reports get the response count.
    assert ("Today: 0 Families responded in the past 24 hours." in html) == (
        role != "ministry_leader"
    )
    # The panel comes after every other panel on Home.
    assert b"<section" not in body[body.index(b"data-next-steps") :]


def test_no_current_campaign_shows_no_next_steps(auth_service, google):
    """With no campaign, Home's campaign panel says what to do instead."""
    browser, response = signed_in()
    assert response.status_code == 302
    body = browser.get("/admin/").content
    assert panel(body) is None
    assert b"No campaign yet" in body or b"no current campaign" in body


def test_active_campaign_steps_counts_and_today(response_service, google):
    """On an active campaign, an Administrator's steps carry the open counts
    and Today counts the Families who responded in the past 24 hours."""
    harness = setup(response_service)
    browser, _, _, _ = leader(harness, google, roles=("administrator",))
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response, body = read(browser, "/admin/")
    assert response.status_code == 200
    html = panel(body)
    listed = steps(html)
    assert [step.split(" — ")[0] for step in listed] == [
        "Response dashboard",
        "Additional information",
        "Ministry requests",
        "Family email progress",
        "Outgoing mail",
    ]
    # The follow-up request the harness made is open in the queue.
    assert "Ministry requests — 2 open" in listed
    assert 'href="/admin/mail/outgoing/"' in html
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
    noun = "Family" if recent == 1 else "Families"
    verb = f"Today: {recent} {noun} responded in the past 24 hours."
    assert verb in html


def test_ministry_leader_today_line_counts_their_open_requests(
    response_service, google
):
    """A leader's Today line sums My Ministries; Next steps link the queue."""
    harness = setup(response_service)
    browser, _, _, _ = leader(harness, google)
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response, body = read(browser, "/admin/")
    assert response.status_code == 200
    html = panel(body)
    assert "Today: 1 open request in your Ministries." in html
    assert "Families responded" not in html
    assert [step.split(" — ")[0] for step in steps(html)] == ["Ministry requests"]


def test_status_read_leaves_the_home_only_values_out(response_service):
    """The command line's status read computes neither Today nor the test email."""
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
