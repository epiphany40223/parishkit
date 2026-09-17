"""Pinned report links and original charts require current report authority."""

import re
import socket
from uuid import uuid4

import pytest
from django.test import Client

from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.campaigns.read_guards import DownloadPool, ReadLimits
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.outbox_models import OutboxMessage

from ..policy_factory import address
from .auth_builders import signed_in
from .campaign_builders import campaign_clock, change
from .response_builders import response_source
from .test_background_grants_postgresql import task_login
from .test_daily_digest_dispatch_postgresql import allocated
from .test_daily_digest_planning_postgresql import INSTANT
from .test_export_views_postgresql import restricted_download_pool
from .test_family_mail_preparation_postgresql import family_mail  # noqa: F401
from .test_recipient_suppressions_postgresql import refresh

pytestmark = pytest.mark.django_db(transaction=True)


def read(browser, path):
    """Use an actual abortable transport through the complete response lifetime."""
    server, peer = socket.socketpair()
    try:
        response = browser.get(path, **{"gunicorn.socket": server})
        assert response.status_code == 200
        assert response["Cache-Control"] == "no-store"
        body = b"".join(response.streaming_content)
        response.close()
        return response, body
    finally:
        server.close()
        peer.close()


def test_report_and_chart_remain_exact_after_source_changes(family_mail, google):  # noqa: F811
    with campaign_clock(INSTANT):
        ready = allocated(family_mail)
        browser, _ = signed_in()
        path = f"/admin/reports/daily-digests/{ready.snapshot_id}/"
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            _, before = read(browser, path)
            _, chart = read(browser, path + "chart.png")
        assert b"Pinned report" in before
        assert b"statistics_inputs" not in before
        assert chart == bytes(ready.chart)
        newer = response_source()
        newer.members[3]["emailAddress"] = "changed@example.org"
        refresh(family_mail, newer)
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            _, after = read(browser, path)
            _, changed_chart = read(browser, path + "chart.png")
        # Chrome carries current activity times; the pinned data JSON does not.
        pattern = rb'<script[^>]+id="digest-chart-data"[^>]*>(.*?)</script>'
        assert re.search(pattern, before, re.S).group(1) == re.search(
            pattern, after, re.S
        ).group(1)
        assert changed_chart == chart
        assert (
            AuditEvent.objects.filter(
                event_type="daily_digest_viewed", subject_id=ready.snapshot_id
            ).count()
            == 8
        )


@pytest.mark.parametrize("role", ["staff", "ministry_leader"])
def test_report_roles_apply_to_html_and_png(family_mail, google, role):  # noqa: F811
    with campaign_clock(INSTANT):
        ready = allocated(family_mail)
        store = family_mail.service.store
        change(
            store,
            store.active(),
            uuid4(),
            [
                {
                    "operation": "add",
                    "section": "login_rules",
                    **address("reader@example.org", roles=(role,)),
                }
            ],
        )
        google[0]["email"] = "reader@example.org"
        browser, _ = signed_in()
        path = f"/admin/reports/daily-digests/{ready.snapshot_id}/"
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            for suffix in ("", "chart.png"):
                if role == "staff":
                    read(browser, path + suffix)
                else:
                    assert browser.get(path + suffix).status_code == 403
            assert browser.get(path + "?snapshot=current").status_code == 400
        assert Client().get(path).status_code in {302, 403}


def test_original_png_download_uses_restricted_read_connection(
    family_mail,  # noqa: F811
    google,
    settings,
):
    with campaign_clock(INSTANT):
        ready = allocated(family_mail)
        browser, _ = signed_in()
        settings.STEWARDSHIP_DOWNLOAD_POOL = DownloadPool(
            ReadLimits(process_pool_size=1)
        )
        with restricted_download_pool(settings):
            response, content = read(
                browser,
                f"/admin/reports/daily-digests/{ready.snapshot_id}/download.png",
            )
        assert content == bytes(ready.chart)
        assert response["Content-Type"] == "image/png"
        assert response["Content-Disposition"].startswith("attachment;")


def test_daily_delivery_pages_do_not_format_a_nonexistent_family_duid(
    family_mail,  # noqa: F811
    google,
):
    with campaign_clock(INSTANT):
        allocated(family_mail)
        message = OutboxMessage.objects.get()
        browser, _ = signed_in()
        with task_login(ServiceRole.WEB, exact=True):
            for path in (
                "/admin/deliveries?state=all",
                f"/admin/deliveries/{message.pk}",
            ):
                response = browser.get(path)
                assert response.status_code == 200
                assert b"Administrator report" in response.content
                assert b"None" not in response.content
