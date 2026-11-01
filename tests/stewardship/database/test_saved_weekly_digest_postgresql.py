"""The saved Weekly digest pages, over retained snapshots (#1836).

Report emails replaced the Weekly digest (#1786), but the page an emailed
digest linked to and its request pages still serve the digests sent before.
The snapshots here are seeded as an upgraded database holds them
(``digest_snapshot_builders``): the observation and the selection are the
live ones, the rest of the rows are written with the digest guards off. The
pages and their reads are the real ones, on the restricted web login.
"""

import re
import socket
from datetime import timedelta
from uuid import UUID

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.reports import weekly_views
from parishkit.stewardship.reports.digest_funnel import digest_funnel

from .auth_builders import signed_in
from .campaign_builders import campaign_clock
from .digest_snapshot_builders import WEEKLY_AT, WEEKLY_CANARY, seed_weekly
from .family_link_checks import link_reads
from .report_builders import set_user
from .test_background_grants_postgresql import task_login
from .test_response_revisit_postgresql import revisit
from .test_response_submission_postgresql import submit
from .test_saved_daily_digest_postgresql import read
from .test_weekly_observation_postgresql import respond

pytestmark = pytest.mark.django_db(transaction=True)


def views(snapshot_id):
    """The weekly page's view events for one snapshot."""
    return AuditEvent.objects.filter(
        event_type="weekly_digest_viewed", subject_id=snapshot_id
    )


def test_weekly_page_and_detail_preserve_capture_but_show_current_status(
    live_response_service, google, monkeypatch
):
    """Later submissions cannot silently rewrite the previously emailed request."""
    harness = live_response_service
    respond(harness, WEEKLY_CANARY)
    with campaign_clock(WEEKLY_AT):
        snapshot = seed_weekly(harness, monkeypatch)
        browser, _ = signed_in()
        path = reverse("admin:weekly_digest_snapshot", args=[snapshot.pk])
        detail = path + f"items/{snapshot.information[0]}/"
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            for url in (path, detail):
                with CaptureQueriesContext(connection) as queries:
                    _, content = read(browser, url)
                assert WEEKLY_CANARY.encode() in content
                # The saved Family name opens its Timeline (#1570); its
                # record id comes with the status read, adding no query.
                assert b'<h2><a href="/admin/reports/families/' in content
                assert link_reads(queries) == []
                assert b"Saved report" in content
                assert b"Current actionable request" in content
                assert b"second@example.org" not in content
        # The detail page's trail is its way back to the report (navigation
        # rule 3), not a link of its own (#521).
        assert f'<li><a href="{path}">Weekly report</a></li>'.encode() in content
        assert b"Return to Weekly report" not in content
        harness, form, answers, _ = revisit(harness)
        answers["additional_information"] = "NEW-PRIVATE-REQUEST"
        submit(harness, form, answers)
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            _, content = read(browser, detail)
        assert b"Superseded by a later response" in content
        assert WEEKLY_CANARY.encode() in content
        assert b"NEW-PRIVATE-REQUEST" not in content
        assert views(snapshot.pk).count() == 6


def test_weekly_list_page_recounts_the_emails_funnel(
    live_response_service, google, monkeypatch
):
    """The list page shows the funnel counted at the capture instant (#477).

    A single request's page shows no funnel. A later submission does not
    change the saved page's funnel, because it is counted at the capture
    instant from durable timestamps.
    """
    harness = live_response_service
    respond(harness, WEEKLY_CANARY)
    with campaign_clock(WEEKLY_AT):
        snapshot = seed_weekly(harness, monkeypatch)
        browser, _ = signed_in()
        path = reverse("admin:weekly_digest_snapshot", args=[snapshot.pk])
        detail = path + f"items/{snapshot.information[0]}/"

        def page_funnel():
            """The list page's funnel panel as (label, families) pairs."""
            with task_login(ServiceRole.WEB, exact=True, reconnect=True):
                _, content = read(browser, path)
            panel = re.search(
                rb'<section class="panel" data-digest-funnel>.*?</section>',
                content,
                re.S,
            )
            assert panel, "The list page has no funnel panel."
            return re.findall(
                rb'<th scope="row">([^<]+).*?</th><td>([^<]+)</td>',
                panel.group(0),
                re.S,
            )

        before = page_funnel()
        rows = {label.decode(): count.decode() for label, count in before}
        assert rows["Submitted"] == "1"
        # No side figures (#1532).
        assert "Submitted more than once" not in rows
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            _, content = read(browser, detail)
        assert b"data-digest-funnel" not in content
    # A second submission an hour after the capture instant is real: counted
    # then, it is the Family's second. The saved page is unchanged.
    later = WEEKLY_AT + timedelta(hours=1)
    with campaign_clock(later):
        harness, form, answers, _ = revisit(harness)
        answers["additional_information"] = "LATER-PRIVATE-REQUEST"
        submit(harness, form, answers)
        assert digest_funnel(harness.campaign.pk, "production", later).submitted_again
        assert page_funnel() == before


def test_a_testing_weekly_page_shows_the_testing_note(
    response_service, google, monkeypatch
):
    """A Testing report's saved page has the Testing note and no funnel counts."""
    with campaign_clock(WEEKLY_AT):
        snapshot = seed_weekly(response_service, monkeypatch)
        browser, _ = signed_in()
        path = reverse("admin:weekly_digest_snapshot", args=[snapshot.pk])
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            _, content = read(browser, path)
    assert b"This report was generated in Testing mode." in content
    assert b"Testing report emails leave out the response funnel." in content
    assert b"data-digest-funnel" not in content


@pytest.mark.parametrize("role", ["staff", "ministry_leader"])
def test_weekly_reports_open_for_staff_only_beyond_administrators(
    live_response_service, google, monkeypatch, role
):
    """Staff read the report and its items; a Ministry leader and anonymous do not.

    Staff may read the requests the report listed (report-emails spec,
    "Saved copies"; ``weekly_views._principal``).
    """
    respond(live_response_service, WEEKLY_CANARY)
    with campaign_clock(WEEKLY_AT):
        snapshot = seed_weekly(live_response_service, monkeypatch)
        set_user(live_response_service.service.store, "reader@example.org", role)
        google[0]["email"] = "reader@example.org"
        browser, _ = signed_in()
        path = reverse("admin:weekly_digest_snapshot", args=[snapshot.pk])
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            for suffix in ("", f"items/{snapshot.information[0]}/"):
                if role == "staff":
                    _, content = read(browser, path + suffix)
                    assert WEEKLY_CANARY.encode() in content
                else:
                    assert browser.get(path + suffix).status_code == 403
                assert Client().get(path + suffix).status_code == 403


def test_weekly_detail_rejects_an_item_the_report_did_not_select(
    live_response_service, google, monkeypatch
):
    """A known item UUID is not a member of a later report that omits it."""
    harness = live_response_service
    respond(harness, WEEKLY_CANARY)
    with campaign_clock(WEEKLY_AT):
        first = seed_weekly(harness, monkeypatch)
        item = first.information[0]
        # The later report follows the first's coverage: the item was
        # reported, so nothing is new and nothing needs correcting.
        empty = seed_weekly(
            harness,
            monkeypatch,
            reported={UUID(item)},
            after=first.submission_watermark,
        )
        browser, _ = signed_in()
        path = reverse("admin:weekly_digest_snapshot", args=[empty.pk])
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            _, content = read(browser, path)
            assert b"No new requests needing follow-up" in content
            assert WEEKLY_CANARY.encode() not in content
            # A supported WSGI socket is required before content selection.
            server, peer = socket.socketpair()
            try:
                response = browser.get(
                    path + f"items/{item}/", **{"gunicorn.socket": server}
                )
                assert response.status_code == 403
                response.close()
            finally:
                server.close()
                peer.close()


def test_weekly_corrections_do_not_repeat_former_text(
    live_response_service, google, monkeypatch
):
    """Both report and detail omit withdrawn text, though its record remains."""
    harness = live_response_service
    respond(harness, WEEKLY_CANARY)
    with campaign_clock(WEEKLY_AT):
        first = seed_weekly(harness, monkeypatch)
        item = first.information[0]
        harness, form, answers, _ = revisit(harness)
        answers["additional_information"] = ""
        submit(harness, form, answers)
        correction = seed_weekly(
            harness,
            monkeypatch,
            reported={UUID(item)},
            after=first.submission_watermark,
        )
        browser, _ = signed_in()
        path = reverse("admin:weekly_digest_snapshot", args=[correction.pk])
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            for url in (path, path + f"items/{item}/"):
                _, content = read(browser, url)
                assert b"Withdrawn by the Family" in content
                assert b"intentionally omits" in content
                assert WEEKLY_CANARY.encode() not in content


def test_weekly_detail_escapes_full_text_and_overview_is_bounded(
    live_response_service, google, monkeypatch
):
    """Long unsafe-looking text is shortened only in the overview and escaped."""
    text = '<script>alert("private")</script>' + "Long private text " * 100
    respond(live_response_service, text)
    with campaign_clock(WEEKLY_AT):
        snapshot = seed_weekly(live_response_service, monkeypatch)
        browser, _ = signed_in()
        path = reverse("admin:weekly_digest_snapshot", args=[snapshot.pk])
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
    respond(live_response_service, WEEKLY_CANARY)
    with campaign_clock(WEEKLY_AT):
        snapshot = seed_weekly(live_response_service, monkeypatch)
        browser, _ = signed_in()
        path = reverse("admin:weekly_digest_snapshot", args=[snapshot.pk])
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
            views(snapshot.pk)
            .order_by("created_at")
            .values_list("auditcontext__context", flat=True)
        ) == [{"outcome": "started"}, {"outcome": "failed"}]
