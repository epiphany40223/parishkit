"""Emailed reports lists each role's past reports and ends Send a weekly report now.

The page (ADM-12.17) links the current campaign's report email sends (#1786)
and its daily digests for Administrators and Staff, and its weekly digests
for Administrators only; Send a weekly report now, Send now of the Weekly
report, returns to it and names the request it made.
"""

from uuid import uuid4

import pytest
from django.urls import reverse

from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.reports.report_email_models import (
    ReportEmailManualRequest,
)

from ..policy_factory import address, assignment
from .auth_builders import signed_in
from .campaign_builders import campaign_clock, change
from .report_builders import (
    DAILY_INSTANT,
    WEEKLY_INSTANT,
    add_digest,
    weekly_allocated,
)
from .report_builders import allocated as daily_allocated
from .test_background_grants_postgresql import task_login
from .test_family_mail_preparation_postgresql import family_mail  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)

PAGE = "admin:emailed_reports"


def _sign_in_as(store, google, email, roles, *records):
    """Add a sign-in rule for ``email`` with ``roles`` and sign in as it.

    For the sign-in rules form, as before users; see ``_sign_in_user``.
    """
    change(
        store,
        store.active(),
        uuid4(),
        [
            {"operation": "add", "section": "login_rules", **record}
            for record in (address(email, roles=roles), *records)
        ],
    )
    google[0]["email"] = email
    browser, _ = signed_in()
    return browser


def _sign_in_user(store, google, email, role):
    """Add a users-form user for ``email`` with ``role`` and sign in as it."""
    from parishkit.stewardship.accounts.user_changes import plan_changes

    records = store.active().document()["sections"]["login_rules"]
    _, patch = plan_changes(records, [{"email": email, "name": "Staff", "role": role}])
    assert change(store, store.active(), uuid4(), patch).state == "applied"
    google[0]["email"] = email
    browser, _ = signed_in()
    return browser


def test_report_emails_are_listed_for_administrators_and_staff(
    family_mail,  # noqa: F811
    google,
):
    """Each role that may open a report email sees it linked; weekly is Admin only."""
    with campaign_clock(DAILY_INSTANT):
        preparation = daily_allocated(family_mail)
        link = reverse("admin:report_email_send", args=[preparation.pk])
        admin, _ = signed_in()
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            page = admin.get(reverse(PAGE))
        assert page.status_code == 200 and page["Cache-Control"] == "no-store"
        assert link.encode() in page.content
        assert b"Weekly reports" in page.content
        assert b"data-weekly-now" in page.content
        staff = _sign_in_user(
            family_mail.service.store, google, "staff@example.org", "staff"
        )
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            page = staff.get(reverse(PAGE))
        assert page.status_code == 200 and link.encode() in page.content
        assert b"Weekly reports" not in page.content
        assert b"data-weekly-now" not in page.content
        assert reverse("admin:weekly_digest_manual").encode() not in page.content


def test_a_ministry_leader_is_refused(response_service, google):
    """A Ministry leader opens no emailed report, so the page is refused."""
    store = response_service.service.store
    leader = _sign_in_as(
        store,
        google,
        "leader@example.org",
        ("ministry_leader",),
        assignment("leader@example.org", ministry=9),
    )
    assert leader.get(reverse(PAGE)).status_code == 403


def test_weekly_reports_are_listed_for_administrators(live_response_service, google):
    """A Weekly report's send is linked on the page for an Administrator."""
    with campaign_clock(WEEKLY_INSTANT):
        preparation = weekly_allocated(live_response_service)
        admin, _ = signed_in()
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            page = admin.get(reverse(PAGE))
        assert page.status_code == 200
        link = reverse("admin:report_email_send", args=[preparation.pk])
        assert link.encode() in page.content
        # Only a closed request id is accepted in the address.
        assert admin.get(reverse(PAGE) + "?search=x").status_code == 400


def test_send_a_weekly_report_now_leads_the_page_and_waits_for_its_schedule(
    response_service, google
):
    """The button is always there for an Administrator, above every list.

    Without a Weekly report it is unavailable, with a hint after it (no
    page can add one until the Report emails page ships, #1786); once the
    report exists it links the request page.
    """
    harness = response_service
    with campaign_clock(WEEKLY_INSTANT):
        browser, _ = signed_in()
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            page = browser.get(reverse(PAGE)).content.decode()
        action = page.index("data-weekly-now")
        assert (
            action
            < page.index('id="report-emails"')
            < page.index('id="daily-reports"')
            < page.index('id="weekly-reports"')
        )
        assert reverse("admin:weekly_digest_manual") not in page
        assert (
            '<button type="button" disabled aria-describedby="weekly-now-reason"'
            in (page)
        )
        assert "Available when the campaign has a Weekly report." in page
        add_digest(harness.service.store, harness.campaign, weekly=True)
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            page = browser.get(reverse(PAGE)).content.decode()
        assert f'href="{reverse("admin:weekly_digest_manual")}"' in page
        assert page.index("data-weekly-now") < page.index('id="report-emails"')
        assert "weekly-now-reason" not in page


def test_send_a_weekly_report_now_returns_to_emailed_reports(response_service, google):
    """Queuing a manual weekly report ends on the page, linking its progress."""
    harness = response_service
    with campaign_clock(WEEKLY_INSTANT):
        add_digest(harness.service.store, harness.campaign, weekly=True)
        configuration = SystemConfiguration.objects.get().active_configuration_id
        browser, _ = signed_in()
        command = uuid4()
        values = {
            "command_id": str(command),
            "configuration_id": str(configuration),
            "acknowledge": "on",
        }
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            browser.get(reverse("admin:weekly_digest_manual"))
            response = browser.post(
                reverse("admin:weekly_digest_manual"),
                values,
                HTTP_X_CSRFTOKEN=browser.cookies["pk_admin_csrf"].value,
            )
            assert response.status_code == 302
            assert response["Location"] == f"{reverse(PAGE)}?requested={command}"
            page = browser.get(response["Location"])
        assert page.status_code == 200
        task = ReportEmailManualRequest.objects.get(pk=command).task_id
        assert reverse("admin:background_task_page", args=[task]).encode() in (
            page.content
        )
        assert b"Your weekly report was requested." in page.content
        # Another request id names nothing here: no notice.
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            other = browser.get(f"{reverse(PAGE)}?requested={uuid4()}")
        assert other.status_code == 200
        assert b"Your weekly report" not in other.content
