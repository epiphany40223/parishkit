"""A report send's saved copy page, as its email was sent (#1786, interim).

Every report email links its send's saved copy
(``/admin/mail/report-emails/sends/<send>/``): until the Report emails page
ships, the page shows the email from the send's snapshot with its chart
images beside it, and a listed request's link opens that request on
Additional information. Administrators and Staff may open it; each opening
is audited with the send only. These replace the digest pages' suites,
whose saved digests no release makes any more.
"""

import re
from uuid import uuid4

import pytest
from django.test import Client
from django.urls import reverse

from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.outbox_models import OutboxMessage
from parishkit.stewardship.reports.report_email_models import ReportEmailSnapshot

from .auth_builders import signed_in
from .campaign_builders import campaign_clock
from .report_builders import DAILY_INSTANT, WEEKLY_INSTANT, set_user, weekly_allocated
from .report_builders import allocated as daily_allocated
from .test_background_grants_postgresql import task_login
from .test_family_mail_preparation_postgresql import family_mail  # noqa: F401
from .test_weekly_observation_postgresql import respond

pytestmark = pytest.mark.django_db(transaction=True)


def copy_path(preparation):
    """The saved copy page of one send."""
    return reverse("admin:report_email_send", args=[preparation.pk])


def test_an_administrator_opens_the_email_and_its_charts(family_mail, google):  # noqa: F811
    """The page draws the snapshot's email with its own chart images."""
    with campaign_clock(DAILY_INSTANT):
        preparation = daily_allocated(family_mail)
        snapshot = ReportEmailSnapshot.objects.get(preparation=preparation)
        browser, _ = signed_in()
        path = copy_path(preparation)
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            page = browser.get(path)
            assert page.status_code == 200
            assert page["Cache-Control"] == "no-store"
            body = page.content.decode()
            names = re.findall(r'src="images/([^"]+)\.png"', body)
            assert names == [cid for cid, _ in snapshot.images]
            assert "cid:" not in body
            image = browser.get(f"{path}images/{names[0]}.png")
            assert image.status_code == 200 and image["Content-Type"] == "image/png"
            assert image.content.startswith(b"\x89PNG")
            assert browser.get(f"{path}images/missing.png").status_code == 403
            # A query string is refused; an unknown send is denied.
            assert browser.get(path + "?view=x").status_code == 400
            unknown = reverse("admin:report_email_send", args=[uuid4()])
            assert browser.get(unknown).status_code == 403
        viewed = AuditEvent.objects.filter(
            event_type="report_email_viewed", subject_id=preparation.pk
        )
        assert viewed.count() == 1


@pytest.mark.parametrize("role", ["staff", "ministry_leader"])
def test_staff_may_open_it_and_a_ministry_leader_may_not(
    family_mail,  # noqa: F811
    google,
    role,
):
    """Every block's reader role is Staff (decision 3); others are refused."""
    with campaign_clock(DAILY_INSTANT):
        preparation = daily_allocated(family_mail)
        set_user(family_mail.service.store, "reader@example.org", role)
        google[0]["email"] = "reader@example.org"
        browser, _ = signed_in()
        path = copy_path(preparation)
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            response = browser.get(path)
        assert response.status_code == (200 if role == "staff" else 403)
        assert Client().get(path).status_code == 403


def test_a_listed_request_opens_on_additional_information(
    live_response_service, google
):
    """Only a request the send listed is redirected to its own page."""
    harness = live_response_service
    respond(harness, "Please call about the parish picnic")
    with campaign_clock(WEEKLY_INSTANT):
        preparation = weekly_allocated(harness)
        snapshot = ReportEmailSnapshot.objects.get(preparation=preparation)
        (listed,) = [
            item[0]
            for block in snapshot.blocks
            if block["block"] == "additional_information" and block["data"]
            for item in block["data"]["items"]
        ]
        browser, _ = signed_in()
        path = copy_path(preparation)
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            response = browser.get(f"{path}items/{listed}/")
            assert response.status_code == 302
            assert response["Location"] == reverse(
                "admin:information_item", kwargs={"item_id": listed}
            )
            assert browser.get(f"{path}items/{uuid4()}/").status_code == 403


def test_sent_mail_names_a_report_email(family_mail, google):  # noqa: F811
    """Sent mail lists and shows a report email by its purpose's name."""
    with campaign_clock(DAILY_INSTANT):
        daily_allocated(family_mail)
        message = OutboxMessage.objects.get(purpose="admin_report")
        browser, _ = signed_in()
        with task_login(ServiceRole.WEB, exact=True):
            for path in (
                "/admin/mail/sent/?state=all",
                f"/admin/mail/sent/{message.pk}/",
            ):
                response = browser.get(path)
                assert response.status_code == 200
                assert b"Report email" in response.content
                assert b"Unknown status" not in response.content
                assert b"<p>Family DUID:" not in response.content
