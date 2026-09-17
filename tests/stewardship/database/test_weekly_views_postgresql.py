"""Weekly email links require current Admin scope and a guarded response lifetime."""

import socket
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from django.test import Client

from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.reports import weekly_views
from parishkit.stewardship.reports.weekly_models import WeeklyDigestRecipient

from ..policy_factory import address
from .auth_builders import signed_in
from .campaign_builders import campaign_clock, change
from .test_background_grants_postgresql import task_login
from .test_daily_digest_views_postgresql import read
from .test_response_revisit_postgresql import revisit
from .test_response_submission_postgresql import submit
from .test_weekly_capture_postgresql import INSTANT
from .test_weekly_coverage_postgresql import accepted, publish, replacement
from .test_weekly_dispatch_postgresql import allocated
from .test_weekly_fanout_postgresql import captured
from .test_weekly_observation_postgresql import respond

pytestmark = pytest.mark.django_db(transaction=True)


def test_weekly_page_and_detail_preserve_capture_but_show_current_status(
    live_response_service, google
):
    """Later submissions cannot silently rewrite the previously emailed request."""
    harness = live_response_service
    with campaign_clock(INSTANT):
        snapshot = allocated(harness)
        browser, _ = signed_in()
        path = f"/admin/reports/weekly-digests/{snapshot.pk}/"
        detail = path + f"items/{snapshot.information[0]}/"
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            for url in (path, detail):
                _, content = read(browser, url)
                assert b"PRIVATE-WEEKLY-DISPATCH-CANARY" in content
                assert b"Pinned report" in content
                assert b"Current actionable request" in content
                assert b"second@example.org" not in content
        harness, form, answers, _ = revisit(harness)
        answers["additional_information"] = "NEW-PRIVATE-REQUEST"
        submit(harness, form, answers)
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            _, content = read(browser, detail)
        assert b"Superseded by a later response" in content
        assert b"PRIVATE-WEEKLY-DISPATCH-CANARY" in content
        assert b"NEW-PRIVATE-REQUEST" not in content
        assert (
            AuditEvent.objects.filter(
                event_type="weekly_digest_viewed", subject_id=snapshot.pk
            ).count()
            == 6
        )


@pytest.mark.parametrize("role", ["staff", "ministry_leader"])
def test_private_weekly_reports_deny_non_admins_and_anonymous(
    live_response_service, google, role
):
    """The scoped Admin digest is distinct from the later shared follow-up queue."""
    with campaign_clock(INSTANT):
        snapshot = allocated(live_response_service)
        store = live_response_service.service.store
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
        path = f"/admin/reports/weekly-digests/{snapshot.pk}/"
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            for suffix in ("", f"items/{snapshot.information[0]}/"):
                assert browser.get(path + suffix).status_code == 403
                assert Client().get(path + suffix).status_code == 403


def test_weekly_detail_rejects_unselected_item_even_in_same_observation(
    live_response_service, google
):
    """A known UUID from an earlier report is not a member of the new empty one."""
    harness = live_response_service
    with campaign_clock(datetime(2026, 10, 8, tzinfo=UTC)):
        snapshot = allocated(harness)
        accepted(WeeklyDigestRecipient.objects.get(snapshot=snapshot))
    with campaign_clock(datetime(2026, 10, 15, tzinfo=UTC)):
        claim, empty = replacement()
        publish(claim)
        browser, _ = signed_in()
        path = f"/admin/reports/weekly-digests/{empty.pk}/"
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            _, content = read(browser, path)
            assert b"no new actionable requests" in content
            assert b"PRIVATE-WEEKLY-DISPATCH-CANARY" not in content
            # A supported WSGI socket is required before content selection.
            server, peer = socket.socketpair()
            try:
                response = browser.get(
                    path + f"items/{snapshot.information[0]}/",
                    **{"gunicorn.socket": server},
                )
                assert response.status_code == 403
                response.close()
            finally:
                server.close()
                peer.close()


def test_weekly_corrections_do_not_repeat_former_text(live_response_service, google):
    """Both report and detail omit withdrawn text, even though its record remains."""
    harness = live_response_service
    with campaign_clock(datetime(2026, 10, 8, tzinfo=UTC)):
        snapshot = allocated(harness)
        accepted(WeeklyDigestRecipient.objects.get(snapshot=snapshot))
    with campaign_clock(datetime(2026, 10, 15, tzinfo=UTC)):
        harness, form, answers, _ = revisit(harness)
        answers["additional_information"] = ""
        submit(harness, form, answers)
        claim, correction = replacement()
        publish(claim)
        browser, _ = signed_in()
        path = f"/admin/reports/weekly-digests/{correction.pk}/"
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            for url in (path, path + f"items/{snapshot.information[0]}/"):
                _, content = read(browser, url)
                assert b"Withdrawn by the Family" in content
                assert b"intentionally omits" in content
                assert b"PRIVATE-WEEKLY-DISPATCH-CANARY" not in content


def test_weekly_detail_escapes_full_text_and_overview_is_bounded(
    live_response_service, google
):
    """Long unsafe-looking text is shortened only in the overview and escaped."""
    text = '<script>alert("private")</script>' + "Long private text " * 100
    respond(live_response_service, text)
    with campaign_clock(INSTANT):
        claim, snapshot = captured(live_response_service)
        publish(claim)
        browser, _ = signed_in()
        path = f"/admin/reports/weekly-digests/{snapshot.pk}/"
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            _, overview = read(browser, path)
            _, detail = read(browser, path + f"items/{snapshot.information[0]}/")
            assert browser.get(path + "?page=01").status_code == 400
            assert browser.get(path + "?page=1&page=2").status_code == 400
            assert browser.get(path + "?include=all").status_code == 400
        assert "…".encode() in overview
        assert b"<script>alert" not in detail
        assert b"&lt;script&gt;alert" in detail
        assert detail.count(b"Long private text") == 100


@pytest.mark.parametrize("stage", ["transport", "authority"])
def test_weekly_private_content_waits_for_response_guard(
    live_response_service, google, monkeypatch, stage
):
    """Missing transport or authority loss cannot evaluate private page content."""
    with campaign_clock(INSTANT):
        snapshot = allocated(live_response_service)
        browser, _ = signed_in()
        path = f"/admin/reports/weekly-digests/{snapshot.pk}/"
        original = weekly_views._principal

        def authorize(*args, **kwargs):
            """Represent role revocation between intent and guarded admission."""
            if kwargs.get("read_only"):
                raise PermissionError
            return original(*args, **kwargs)

        def forbidden(*args, **kwargs):
            """Report inputs may not be selected before read admission succeeds."""
            raise AssertionError("Private content was opened without authority.")

        monkeypatch.setattr(weekly_views, "snapshot_context", forbidden)
        if stage == "authority":
            monkeypatch.setattr(weekly_views, "_principal", authorize)
        server, peer = socket.socketpair()
        try:
            with task_login(ServiceRole.WEB, exact=True, reconnect=True):
                response = browser.get(
                    path,
                    **({"gunicorn.socket": server} if stage == "authority" else {}),
                )
                assert response.status_code == (403 if stage == "authority" else 503)
                assert response["Cache-Control"] == "no-store"
                response.close()
        finally:
            server.close()
            peer.close()
        assert list(
            AuditEvent.objects.filter(
                event_type="weekly_digest_viewed", subject_id=snapshot.pk
            )
            .order_by("created_at")
            .values_list("auditcontext__context", flat=True)
        ) == [
            {"outcome": "started"},
            {"outcome": "failed"},
        ]
