"""Campaign-end Next steps counts under the real web role (#1590).

Home offers Administrators and Staff Census changes, Roster changes to enter,
Additional information and Ministry requests, each with a count, and each
count is the number of rows its link opens by default: the menu's Census
changes count is the page's Work to do view, and Home's Roster changes to
enter count is that list's Not yet entered view, both read through the
pages' own row selection. A Ministry leader gets neither count, and both
pages refuse them.
"""

import re
from types import SimpleNamespace
from uuid import uuid4

import pytest

from parishkit.stewardship.accounts import admin_context
from parishkit.stewardship.accounts.admin_dashboard import roster_todo
from parishkit.stewardship.accounts.policy import Principal
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.reports.ministry_roster_views import roster_rows, to_enter

from .response_builders import activate_response_service
from .test_background_grants_postgresql import task_login
from .test_census_changes_postgresql import ADMIN, LEADER, STAFF, submit_live, worklist
from .test_ministry_exports_postgresql import leader
from .test_ministry_followup_postgresql import _menu_count
from .test_ministry_reports_postgresql import actor
from .test_ministry_responses_postgresql import start
from .test_report_workspace_postgresql import read
from .test_roster_entries_postgresql import resolved, tick

pytestmark = pytest.mark.django_db(transaction=True)

CENSUS = [SimpleNamespace(name="census_changes", url="/admin/reports/census/")]


def census_count(harness, principal, items=CENSUS):
    """The menu's Census changes count for ``principal``, on the web login."""
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        return admin_context._open_counts(
            SimpleNamespace(), principal, items, harness.campaign
        ).get("census_changes")


def steps(body):
    """Home's Next steps, each step's text with tags stripped, in order."""
    return [
        re.sub(r"<[^>]+>", "", item)
        for item in re.findall(r"<li data-next-step>(.*?)</li>", body.decode(), re.S)
    ]


def test_census_count_is_the_pages_default_rows_by_role(response_service):
    """Staff count the To do rows, an Administrator To review too; a
    Ministry leader, whom the page refuses, gets no count even when asked."""
    harness = response_service
    start(harness)
    harness = activate_response_service(harness)
    submit_live(harness)
    # One By-hand Family address (To do) and one automatic rename (To review).
    for principal, expected in ((STAFF, 1), (ADMIN, 2)):
        assert len(worklist(harness, principal)["rows"]) == expected
        assert census_count(harness, principal) == expected
    with pytest.raises(PermissionError):
        worklist(harness, LEADER)
    assert census_count(harness, LEADER) is None


def test_home_and_menu_show_the_census_count_for_an_administrator(
    response_service, google
):
    """Signed in, the menu and Home's Next steps carry the same Census
    changes count as the page's default view, as "N to do"."""
    harness = response_service
    start(harness)
    harness = activate_response_service(harness)
    submit_live(harness)
    browser, _, _, _ = leader(harness, google, roles=("administrator",))
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response, body = read(browser, "/admin/")
    assert response.status_code == 200
    expected = len(worklist(harness, ADMIN)["rows"])
    assert expected == 2
    assert _menu_count(body, "Census changes") == expected
    assert f"Census changes — {expected} to do" in steps(body)
    assert 'href="/admin/reports/census/"' in body.decode()


def test_roster_count_is_the_lists_not_yet_entered_rows(response_service):
    """Home's Roster changes to enter count follows the list: one resolved
    join to enter, none once it is ticked, again one once cleared. A Ministry
    leader is refused, by the count as by the page."""
    harness, admin, join, _ = resolved(response_service)
    staff = actor(harness, ("staff",), ())
    administrator = actor(harness, ("administrator",), ())
    own = actor(harness, ("ministry_leader",), (9,))
    campaign = Campaign.objects.select_related("active_configuration").get(
        pk=harness.campaign.pk
    )

    def counts(principal):
        """The list's default rows and both counts, on the web login."""
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            rows, _ = roster_rows(campaign.pk, principal, "todo")
            return (
                len(rows),
                to_enter(campaign.pk, principal),
                roster_todo(principal, campaign),
            )

    for principal in (staff, administrator):
        assert counts(principal) == (1, 1, 1)
    tick(harness, admin, join, True)
    assert counts(staff) == (0, 0, 0)
    tick(harness, admin, join, False)
    assert counts(staff) == (1, 1, 1)
    with (
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        pytest.raises(PermissionError),
    ):
        to_enter(campaign.pk, own)
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        assert roster_todo(own, campaign) is None
        # Python claiming Staff for a portal user SQL does not know reads
        # nothing: the selection refuses, and Home shows no number.
        stranger = Principal(uuid4(), frozenset({"staff"}))
        assert roster_todo(stranger, campaign) is None
        # A draft has nothing to enter yet: Home does not read the count.
        draft = SimpleNamespace(
            pk=campaign.pk,
            state="draft",
            active_configuration=campaign.active_configuration,
        )
        assert roster_todo(staff, draft) is None


def test_staff_home_lists_the_campaign_end_work_with_counts(response_service, google):
    """Staff see the four work lists after the Response dashboard, each with
    the count of the rows its link opens; a leader sees only Ministry
    requests with its open count."""
    harness, _, _, _ = resolved(response_service)
    browser, _, _, _ = leader(harness, google, roles=("staff",))
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response, body = read(browser, "/admin/")
    assert response.status_code == 200
    listed = steps(body)
    assert [step.split(" — ")[0] for step in listed] == [
        "Response dashboard",
        "Census changes",
        "Roster changes to enter",
        "Additional information",
        "Ministry requests",
    ]
    assert "Roster changes to enter — 1 not yet entered" in listed
    # The harness's leave request is still open; its join was resolved.
    assert "Ministry requests — 1 open" in listed
    assert _menu_count(body, "Ministry requests") == 1
    assert 'href="/admin/reports/ministries/follow-up/roster/"' in body.decode()


def test_roster_count_database_error_leaves_the_link_without_a_number(
    response_service, google, monkeypatch, caplog
):
    """A database error reading the Roster count does not fail Home: the step
    still links to the list with no number, and the failure is logged at
    WARNING as report_shaping_failed."""
    from django.db import DatabaseError

    from parishkit.stewardship.reports import ministry_roster_views

    harness, _, _, _ = resolved(response_service)
    browser, _, _, _ = leader(harness, google, roles=("staff",))

    def broken(campaign_id, principal):
        """Fail the count's read the way a lost connection or bad SQL would."""
        raise DatabaseError("count unavailable")

    monkeypatch.setattr(ministry_roster_views, "to_enter", broken)
    with (
        caplog.at_level("WARNING", logger="parishkit.stewardship"),
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
    ):
        response, body = read(browser, "/admin/")
    assert response.status_code == 200
    assert "Roster changes to enter" in steps(body)
    assert 'href="/admin/reports/ministries/follow-up/roster/"' in body.decode()
    (warning,) = [
        r for r in caplog.records if r.getMessage() == "report_shaping_failed"
    ]
    assert warning.levelname == "WARNING"
