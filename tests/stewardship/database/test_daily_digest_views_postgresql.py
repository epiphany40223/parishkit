"""Pinned report links and original charts require current report authority."""

import re
import socket
from uuid import uuid4

import pytest
from django.db import DatabaseError
from django.test import Client

from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.audit.schemas import Outcome
from parishkit.stewardship.campaigns.read_guards import DownloadPool, ReadLimits
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.outbox_models import OutboxMessage
from parishkit.stewardship.observability import Event
from parishkit.stewardship.reports import digest_views
from parishkit.stewardship.reports.digest_capture import capture_daily_snapshot
from parishkit.stewardship.storage import StorageInvariantError

from ..policy_factory import address
from .auth_builders import signed_in
from .campaign_builders import campaign_clock, change
from .response_builders import response_source
from .test_background_grants_postgresql import task_login
from .test_daily_digest_capture_postgresql import prepare
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
def test_report_roles_apply_to_all_representations(family_mail, google, role, settings):  # noqa: F811
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
        settings.STEWARDSHIP_DOWNLOAD_POOL = DownloadPool(
            ReadLimits(process_pool_size=1)
        )
        with restricted_download_pool(settings):
            for suffix in ("", "chart.png", "download.png"):
                if role == "staff":
                    response, _ = read(browser, path + suffix)
                    if suffix == "download.png":
                        assert response["Content-Disposition"].startswith("attachment;")
                else:
                    assert browser.get(path + suffix).status_code == 403
            assert browser.get(path + "?snapshot=current").status_code == (
                400 if role == "staff" else 403
            )
        for suffix in ("", "chart.png", "download.png", "?snapshot=current"):
            assert Client().get(path + suffix).status_code == 403


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


@pytest.mark.parametrize("suffix", ["", "chart.png", "download.png"])
def test_captured_but_unbuilt_report_is_temporarily_unavailable(
    family_mail,  # noqa: F811
    google,
    settings,
    suffix,
):
    """A valid retained ID cannot turn unfinished rendering into a server error."""
    with campaign_clock(INSTANT):
        claim = prepare(family_mail)
        with task_login(ServiceRole.WORKER, exact=True), work_transaction():
            retained = capture_daily_snapshot(claim)
        browser, _ = signed_in()
        settings.STEWARDSHIP_DOWNLOAD_POOL = DownloadPool(
            ReadLimits(process_pool_size=1)
        )
        with restricted_download_pool(settings):
            response = browser.get(
                f"/admin/reports/daily-digests/{retained.pk}/{suffix}"
            )
        assert response.status_code == 503
        assert response["Cache-Control"] == "no-store"
        assert b"Pinned report" not in response.content


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
                assert b"Daily Administrator report" in response.content
                assert b"Unknown status" not in response.content
                assert not re.search(rb"<h2>.*Family DUID", response.content)
                assert b"<p>Family DUID:" not in response.content


@pytest.mark.parametrize("stage", ["denied", "unavailable", "streamed"])
@pytest.mark.parametrize("error_type", [DatabaseError, StorageInvariantError])
def test_terminal_audit_failure_preserves_response_and_emits_safe_signal(
    family_mail,  # noqa: F811
    google,
    monkeypatch,
    caplog,
    stage,
    error_type,
):
    """Neither admission failure nor stream closure exposes audit exception text."""
    original = digest_views.record_action

    def record(*args, **kwargs):
        """Keep actual durable STARTED evidence but fail the terminal write."""
        if kwargs["context"]["outcome"] != Outcome.STARTED:
            raise error_type("private report data must not be logged")
        return original(*args, **kwargs)

    def denied(*args, **kwargs):
        """Simulate authority loss between intent and guarded admission."""
        raise PermissionError

    with campaign_clock(INSTANT):
        ready = allocated(family_mail)
        browser, _ = signed_in()
        monkeypatch.setattr(digest_views, "record_action", record)
        if stage == "denied":
            monkeypatch.setattr(digest_views, "campaign_response", denied)
        path = f"/admin/reports/daily-digests/{ready.snapshot_id}/chart.png"
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            if stage == "streamed":
                _, body = read(browser, path)
                assert body == bytes(ready.chart)
            else:
                # Missing the server-owned socket exercises the real sanitized
                # unavailable path; denied instead simulates current role loss.
                response = browser.get(path)
                assert response.status_code == (403 if stage == "denied" else 503)
                assert response["Cache-Control"] == "no-store"
        outcomes = list(
            AuditEvent.objects.filter(
                event_type="daily_digest_viewed", subject_id=ready.snapshot_id
            ).values_list("auditcontext__context", flat=True)
        )
        assert outcomes == [{"outcome": Outcome.STARTED}]
        assert sum(r.msg == Event.REPORT_AUDIT_FAILED for r in caplog.records) == 1
        assert "private report data" not in caplog.text
