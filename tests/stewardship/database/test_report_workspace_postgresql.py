"""Actual-role report navigation, exact generation display and private audit."""

import re
import socket
from uuid import UUID, uuid4

import pytest
from django.db import connection
from django.urls import reverse

from parishkit.stewardship.accounts.models import PortalSession
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.audit.models import AuditContext, AuditEvent
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.reports.export_models import ExportPublication, ExportRequest

from ..export_urls import export_action
from .auth_builders import signed_in
from .campaign_builders import campaign_clock
from .test_background_grants_postgresql import task_login
from .test_daily_digest_dispatch_postgresql import allocated
from .test_daily_digest_planning_postgresql import INSTANT
from .test_export_views_postgresql import post, restricted_download_pool
from .test_family_mail_preparation_postgresql import family_mail  # noqa: F401
from .test_report_selection_postgresql import pointer

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def http_scenario(family_mail, google):  # noqa: F811
    """Use a complete census capture, not the storage-only fact fixture."""
    with campaign_clock(INSTANT):
        ready = allocated(family_mail)
        browser, _ = signed_in()
        yield (family_mail.service.store, None, ready.fact_set, None), browser


def read(browser, path):
    """Consume the real response-owned transaction with an abortable transport."""
    server, peer = socket.socketpair()
    try:
        response = browser.get(path, **{"gunicorn.socket": server})
        if response.streaming:
            assert connection.in_atomic_block
            content = b"".join(response.streaming_content)
            response.close()
        else:
            content = response.content
        assert not connection.in_atomic_block
        return response, content
    finally:
        server.close()
        peer.close()


def test_workspace_navigation_exact_chart_and_safe_filters(http_scenario, monkeypatch):  # noqa: F811
    """One setup covers real admission, complete HTML/PNG and invalid queries."""
    setup, browser = http_scenario
    campaign_id = setup[2].campaign_id
    pointer(setup[2])
    before = SystemConfiguration.objects.get().current_campaign_id
    path = reverse("admin:participation")
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        redirected = browser.get(reverse("admin:reports") + "?scope=historical")
        assert redirected.status_code == 302
        assert (
            path in redirected["Location"]
            and "scope=historical" in redirected["Location"]
        )
        assert "timezone=" not in redirected["Location"]
        response, html = read(browser, path)
        assert response.status_code == 200
        assert response["Cache-Control"] == "no-store"
        assert b"Updating" in html
        assert b"Current active population statistics" in html
        assert b"ParishSoft data load" in html
        assert b"Historical as of day" in html
        assert str(setup[2].pk).encode() in html
        chart_path = (
            re.search(rb'<img class="digest-chart" src="([^"]+)"', html)
            .group(1)
            .decode()
            .replace("&amp;", "&")
        )
        chart_response, chart = read(browser, chart_path)
        assert chart_response.status_code == 200
        assert chart.startswith(b"\x89PNG")
        _, missing = read(browser, path + "?scope=current")
        assert b"No complete report is ready" in missing
        for query in (
            "scope=all",
            "page=0",
            "page=10001",
            "page=01",
            "sort=name",
            "scope=current&scope=historical",
            "family=Private-name",
            "timezone=missing",
        ):
            response, _ = read(browser, path + "?" + query)
            assert response.status_code == 400
    assert SystemConfiguration.objects.get().current_campaign_id == before
    events = AuditEvent.objects.filter(event_type="participation_viewed")
    assert events.count() == 6
    assert all(
        event.ownership_scope == "parish" and event.campaign_reference == campaign_id
        for event in events
    )
    assert all(
        set(value) == {"outcome"}
        for value in AuditContext.objects.filter(event__in=events).values_list(
            "context", flat=True
        )
    )
    from parishkit.stewardship.reports import workspace_views

    assert events.count() == 6

    from parishkit.stewardship.reports import read_admission

    from .campaign_builders import restored_runtime

    def closed_admission(*args, **kwargs):
        """Close lifecycle admission after the guard's own initial state check."""
        raise PermissionError("private lifecycle detail")

    with monkeypatch.context() as patch:
        patch.setattr(read_admission, "admit_campaign", closed_admission)
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            for endpoint in (reverse("admin:reports"), path, chart_path):
                response, body = read(browser, endpoint)
                assert response.status_code == 503, endpoint
                assert response["Retry-After"] == "5"
                assert b"private lifecycle detail" not in body
    assert events.count() == 6

    # Existing middleware routes an already-restoring deployment to maintenance.
    # The admission unit cases separately cover closure after that middleware.
    with (
        restored_runtime(INSTANT),
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
    ):
        for endpoint in (
            reverse("admin:reports"),
            path,
            chart_path,
        ):
            response, _ = read(browser, endpoint)
            assert response.status_code == 302, endpoint
            assert response["Location"] == "/admin/maintenance"
            assert "Retry-After" not in response
    assert events.count() == 6

    # Use a smaller page budget to exercise actual pagination with this small
    # source fixture. Selection, ordering, rows and URL construction stay real.
    # A 2-row page size is added to the accepted sizes for this test only.
    from parishkit.stewardship.reports import workspace
    from parishkit.stewardship.web import tables

    monkeypatch.setattr(tables, "PAGE_SIZES", (2, *tables.PAGE_SIZES))
    monkeypatch.setattr(workspace, "PAGE_SIZES", (2, *tables.PAGE_SIZES))
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        options = "?sort=date_desc&inactive=yes&size=2"
        # A bookmark from before #728 removed the inactive subtotal still
        # loads: the parameter is ignored and no subtotal is shown.
        response, page_one = read(browser, path + options)
        assert response.status_code == 200
        assert b"Inactive subtotal" not in page_one
        assert b'name="inactive"' not in page_one
        assert b'rel="next"' in page_one and b'rel="prev"' not in page_one
        # Navigator links keep every validated report option.
        assert b"sort=date_desc" in page_one and b"inactive=" not in page_one
        _, page_two = read(browser, path + options + "&page=2")
        assert b'rel="prev"' in page_two and b'rel="next"' in page_two
        # Table dates use the compact parish style ("Oct 31, 2054").
        days_one = [
            int(day) for day in re.findall(rb"<td>Oct (\d+), 2054</td>", page_one)
        ]
        days_two = [
            int(day) for day in re.findall(rb"<td>Oct (\d+), 2054</td>", page_two)
        ]
        assert len(days_one) == len(days_two) == 2
        assert days_one + days_two == sorted(days_one + days_two, reverse=True)
        # Every column heading sorts on the server; the date heading is the
        # sorted one here and says so.
        assert b'aria-sort="descending"' in page_one and b"Page 1 of " in page_one
        response, by_count = read(browser, path + "?sort=-first&size=2")
        assert response.status_code == 200 and b"sort=first" in by_count
        assert read(browser, path + "?sort=first_responses")[0].status_code == 400
        # An unrelated discovery result must never change this report's guard.
        monkeypatch.setattr(workspace_views, "campaign_ids", lambda: (uuid4(),))
        assert read(browser, path)[0].status_code == 200
    from django.db import DatabaseError

    from parishkit.stewardship.reports import export_ui
    from parishkit.stewardship.reports.facts import FactUnavailable

    def unavailable():
        """A backend fault carries private detail that must never reach the page."""
        raise DatabaseError("private source data")

    with monkeypatch.context() as patch:
        patch.setattr(export_ui, "runtime", unavailable)
        for method, endpoint in (
            (browser.get, reverse("admin:report_export", args=[uuid4()])),
            (lambda url: post(browser, url), reverse("admin:report_export_create")),
            (
                lambda url: post(browser, url),
                reverse("admin:report_export_cancel", args=[uuid4()]),
            ),
        ):
            response = method(endpoint)
            assert response.status_code == 503 and response["Retry-After"] == "5"
            assert b"temporarily unavailable" in response.content
            assert b"private source data" not in response.content

    def compacted(*args, **kwargs):
        """Lose the selected generation between display and export admission."""
        raise FactUnavailable("private source data")

    monkeypatch.setattr(export_ui, "create_export", compacted)
    response = post(
        browser,
        reverse("admin:report_export_create"),
        {
            "fact_set_id": str(setup[2].pk),
            "format": "csv",
            "browser_timezone": "UTC",
            "request_key": str(uuid4()),
        },
    )
    assert (
        response.status_code == 503 and b"private source data" not in response.content
    )


def test_native_export_creation_status_cancel_and_restricted_download(
    http_scenario, settings, tmp_path
):
    """One real workflow covers CSRF, replay, passive polling, XLSX and busy retry."""
    from parishkit.stewardship.campaigns.read_guards import DownloadPool, ReadLimits

    setup, browser = http_scenario
    facts = setup[2]
    root = tmp_path / "native-reports"
    root.mkdir(mode=0o700)
    settings.STEWARDSHIP_REPORTS_ROOT = root
    settings.STEWARDSHIP_DOWNLOAD_POOL = DownloadPool(ReadLimits(process_pool_size=1))
    path = reverse("admin:report_export_create")
    values = {
        "fact_set_id": str(facts.pk),
        "format": "xlsx",
        "browser_timezone": "UTC",
        "request_key": str(uuid4()),
    }
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        assert browser.post(path, values).status_code == 403
        response = post(browser, path, values)
        assert response.status_code == 302
        assert post(browser, path, values)["Location"] == response["Location"]
        status_path = response["Location"]
        previous = list(
            PortalSession.objects.values_list("id", "last_activity_at", "version")
        )
        response, body = read(browser, status_path)
        assert response.status_code == 200
        assert b"queued" in body
        assert b"Cancel export" in body
        assert (
            list(PortalSession.objects.values_list("id", "last_activity_at", "version"))
            == previous
        )
        assert (
            post(
                browser,
                export_action(status_path, "retry"),
                {"request_key": str(uuid4())},
            ).status_code
            == 409
        )
    job = ExportRequest.objects.get(request_key=UUID(values["request_key"]))
    from parishkit.stewardship.jobs.dispatch import execute_hint
    from parishkit.stewardship.jobs.queues import WorkQueue
    from parishkit.stewardship.jobs.storage import _status
    from parishkit.stewardship.reports.export_tasks import export_handler

    from .test_taskrun_postgresql import act

    claimed = act(_status(job.task), "claim")
    act(claimed, "permanent_failure")
    retry_key = uuid4()
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        _, failed = read(browser, status_path)
        assert b"Retry export" in failed
        for _ in range(2):
            assert (
                post(
                    browser,
                    export_action(status_path, "retry"),
                    {"request_key": str(retry_key)},
                ).status_code
                == 302
            )
    retry = job.task.chain_runs.get(retry_command_id=retry_key)
    with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
        assert execute_hint(
            retry.pk,
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={"report_export": export_handler(store=setup[0], root=root)},
        )
    publication = ExportPublication.objects.get(request=job)
    with restricted_download_pool(settings):
        pool = settings.STEWARDSHIP_DOWNLOAD_POOL
        pool.acquire()
        try:
            response = post(browser, export_action(status_path, "download"))
            assert response.status_code == 503
            assert response["Retry-After"] == "5"
            assert b"not been discarded" in response.content
        finally:
            pool.release()
        server, peer = socket.socketpair()
        try:
            response = post(
                browser,
                export_action(status_path, "download"),
                **{"gunicorn.socket": server},
            )
            assert response.status_code == 200
            body = b"".join(response.streaming_content)
            response.close()
            assert body.startswith(b"PK")
            assert response["Content-Disposition"].startswith("attachment;")
            assert len(body) == publication.size
        finally:
            server.close()
            peer.close()
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        cancelled = post(browser, path, values | {"request_key": str(uuid4())})[
            "Location"
        ]
        assert post(browser, export_action(cancelled, "cancel")).status_code == 302
        _, body = read(browser, cancelled)
        assert b"cancelled" in body
    from .test_export_cleanup_postgresql import expire_publication

    expire_publication(job)
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        _, expired = read(browser, status_path)
        assert b"file has expired" in expired and b"Download export" not in expired
        gated_path = post(browser, path, values | {"request_key": str(uuid4())})[
            "Location"
        ]
    from django.db import transaction

    from parishkit.stewardship.campaigns.runtime_models import CampaignWorkGate

    # BG-11 still owns initiating purge. Install only its future sentinel as
    # schema owner, then exercise the actual SQL/WEB boundary with guards on.
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            "ALTER TABLE stewardship_campaign_work_gate DISABLE TRIGGER USER"
        )
        CampaignWorkGate.objects.create(
            campaign_id=facts.campaign_id,
            request_id=uuid4(),
            initiated_by_id=uuid4(),
            state="preparing",
        )
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
        cursor.execute("ALTER TABLE stewardship_campaign_work_gate ENABLE TRIGGER USER")
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response, gated = read(browser, gated_path)
        assert response.status_code == 200 and b"Other campaign work" in gated
        # Attribute order follows the template (the theme adds a class).
        assert re.search(rb'<button type="submit"[^>]* disabled[ >]', gated)
        assert post(browser, export_action(gated_path, "cancel")).status_code == 403


def test_staff_report_access_then_ministry_role_and_revocation(http_scenario, google):
    """An actual role replacement blocks the report, image and retained export."""
    from ..policy_factory import address
    from .campaign_builders import change

    setup, admin = http_scenario
    store, _, facts, _ = setup
    pointer(facts)
    rule = address("reader@example.org", roles=("staff", "ministry_leader"))
    change(
        store,
        store.active(),
        uuid4(),
        [{"operation": "add", "section": "login_rules", **rule}],
    )
    google[0]["email"] = "reader@example.org"
    browser, _ = signed_in()
    path = reverse("admin:participation")
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response, _ = read(browser, path)
        assert response.status_code == 200
        response = post(
            browser,
            reverse("admin:report_export_create"),
            {
                "fact_set_id": str(facts.pk),
                "format": "csv",
                "browser_timezone": "UTC",
                "request_key": str(uuid4()),
            },
        )
        assert response.status_code == 302
        job_path = response["Location"]
    change(
        store,
        store.active(),
        uuid4(),
        [
            {
                "operation": "update",
                "section": "login_rules",
                "id": rule["id"],
                "values": {
                    "roles": ["ministry_leader"],
                    "grants": {
                        "ministry_leader": rule["values"]["grants"]["ministry_leader"]
                    },
                },
            }
        ],
    )
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        for url in (reverse("admin:reports"), path, f"{path}{facts.pk}.png", job_path):
            response, _ = read(browser, url)
            assert response.status_code == 403
        assert post(browser, export_action(job_path, "cancel")).status_code == 403
        assert post(browser, export_action(job_path, "download")).status_code == 403
