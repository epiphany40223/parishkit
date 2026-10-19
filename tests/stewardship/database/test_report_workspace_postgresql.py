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


def read(browser, path, **headers):
    """Consume the real response-owned transaction with an abortable transport.

    ``headers`` are extra request headers, by their WSGI names.
    """
    server, peer = socket.socketpair()
    try:
        response = browser.get(path, **{"gunicorn.socket": server}, **headers)
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
    """One setup covers real admission, complete HTML and invalid queries."""
    setup, browser = http_scenario
    campaign_id = setup[2].campaign_id
    pointer(setup[2])
    before = SystemConfiguration.objects.get().current_campaign_id
    path = reverse("admin:response_dashboard")
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
        assert b'aria-label="Active population"' in html
        # One time line, "Data as of"; no Technical details (#1537).
        assert b"Data as of" in html and b"ParishSoft data load" not in html
        assert b"Historical as of day" in html
        assert str(setup[2].pk).encode() in html
        # The daily chart is drawn in the browser from its embedded spec
        # (#720); the page has no chart image to fetch.
        assert b'id="daily-participation-spec"' in html
        assert b"digest-chart" not in html and b"<img" not in html
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
        ):
            response, _ = read(browser, path + "?" + query)
            assert response.status_code == 400
    assert SystemConfiguration.objects.get().current_campaign_id == before
    events = AuditEvent.objects.filter(event_type="participation_viewed")
    assert events.count() == 4
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

    assert events.count() == 4

    from parishkit.stewardship.reports import read_admission

    from .campaign_builders import restored_runtime

    def closed_admission(*args, **kwargs):
        """Close lifecycle admission after the guard's own initial state check."""
        raise PermissionError("private lifecycle detail")

    with monkeypatch.context() as patch:
        patch.setattr(read_admission, "admit_campaign", closed_admission)
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            for endpoint in (reverse("admin:reports"), path):
                response, body = read(browser, endpoint)
                assert response.status_code == 503, endpoint
                assert response["Retry-After"] == "5"
                assert b"private lifecycle detail" not in body
    assert events.count() == 4

    # Existing middleware routes an already-restoring deployment to maintenance.
    # The admission unit cases separately cover closure after that middleware.
    with (
        restored_runtime(INSTANT),
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
    ):
        for endpoint in (reverse("admin:reports"), path):
            response, _ = read(browser, endpoint)
            assert response.status_code == 302, endpoint
            assert response["Location"] == "/admin/maintenance"
            assert "Retry-After" not in response
    assert events.count() == 4

    # Use a smaller page budget to exercise actual pagination with this small
    # source fixture. Selection, ordering, rows and URL construction stay real.
    # A 2-row page size is added to the daily table's accepted sizes for
    # this test only.
    from parishkit.stewardship.reports import workspace

    sizes = (2, *workspace.DAILY_SIZES)
    monkeypatch.setattr(workspace, "DAILY_SIZES", sizes)
    monkeypatch.setattr(workspace_views, "DAILY_SIZES", sizes)
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
        # A cancelled export has ended (#1398): the fixed 410 page.
        response, body = read(browser, cancelled)
        assert (
            response.status_code == 410
            and b"This export is no longer available." in body
        )
    from .test_export_cleanup_postgresql import expire_publication

    expire_publication(job)
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response, expired = read(browser, status_path)
        assert (
            response.status_code == 410
            and b"This export is no longer available." in expired
        )
        assert b"Download export" not in expired
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
    path = reverse("admin:response_dashboard")
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
        for url in (reverse("admin:reports"), path, job_path):
            response, _ = read(browser, url)
            assert response.status_code == 403
        assert post(browser, export_action(job_path, "cancel")).status_code == 403
        assert post(browser, export_action(job_path, "download")).status_code == 403


def test_figures_status_is_passive_and_rereads_never_renew(http_scenario):
    """The Response dashboard and Campaign comparison follow their newest
    figures (#1586): the figures status they poll says only whether newer
    figures than the page's are ready, and is never audited; the page's one
    re-read when they are (sent with X-ParishKit-Read: passive) is audited as
    a view but, like the status reads, never renews the Admin sign-in."""
    setup, browser = http_scenario
    fact_set = setup[2]
    pointer(fact_set)
    status = reverse("admin:response_dashboard_status")
    session = PortalSession.objects.get(revoked_at__isnull=True)
    # Renewing the sign-in sets its last activity and bumps its version; the
    # clock is fixed here, so the version is what shows a renewal.
    before = (session.last_activity_at, session.version)
    events = AuditEvent.objects.filter(
        event_type__in=("participation_viewed", "campaign_comparison_viewed")
    )
    participation = (
        reverse("admin:response_dashboard") + "?scope=historical&timezone=UTC"
    )
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        # The page shows the published figures, which are still Updating: the
        # region keeps being watched at the fast pace and asks for no re-read.
        shown = f"?view=dashboard&scope=historical&shown={fact_set.pk}"
        response = browser.get(status + shown)
        assert response.status_code == 200
        assert response["Cache-Control"] == "no-store"
        assert b'data-live-status="figures"' in response.content
        assert b"data-live-pending" in response.content
        assert b"data-live-interval" not in response.content
        assert b"Updating" in response.content
        assert b"data-reread" not in response.content
        # A page that shows no figures yet (or older ones) is told to re-read
        # itself into the comparison's region.
        response = browser.get(status + "?view=comparison&scope=historical&shown=")
        assert response.status_code == 200
        assert b"data-live-pending" not in response.content
        assert b'data-reread href="#comparison-report"' in response.content
        for query in (
            "view=other&scope=historical&shown=",
            "view=dashboard&scope=all&shown=",
            "view=dashboard&scope=historical&shown=not-a-uuid",
            "view=dashboard&scope=historical&shown=&day=3",
        ):
            assert browser.get(status + "?" + query).status_code == 400, query
        assert events.count() == 0
        # The pages' own re-reads: passive, but audited (two events a view).
        for path in (participation, reverse("admin:campaign_comparison")):
            response, html = read(browser, path, HTTP_X_PARISHKIT_READ="passive")
            assert response.status_code == 200, path
            assert b'data-live-status="figures"' in html
        assert events.count() == 4
        session.refresh_from_db()
        assert (session.last_activity_at, session.version) == before
        # The reader's own load of the page still renews it.
        assert read(browser, participation)[0].status_code == 200
    session.refresh_from_db()
    assert session.version > before[1]


def test_figures_status_refuses_signed_out_and_unauthorized_readers(
    http_scenario, google
):
    """The figures status (#1586) is a report read like the pages it serves:
    a signed-out caller and a signed-in user without report access (a
    Ministry leader) are refused before any option is looked at, and nothing
    is audited as a report view."""
    from django.test import Client

    from ..policy_factory import address
    from .campaign_builders import change

    setup, _ = http_scenario
    store, _, facts, _ = setup
    pointer(facts)
    status = reverse("admin:response_dashboard_status")
    query = f"?view=dashboard&scope=historical&shown={facts.pk}"
    rule = address("leader@example.org", roles=("ministry_leader",))
    change(
        store,
        store.active(),
        uuid4(),
        [{"operation": "add", "section": "login_rules", **rule}],
    )
    google[0]["email"] = "leader@example.org"
    leader, _ = signed_in()
    events = AuditEvent.objects.filter(
        event_type__in=("participation_viewed", "campaign_comparison_viewed")
    )
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        assert Client().get(status + query).status_code == 403
        # Refused before the options are validated: never a 400.
        assert Client().get(status + "?view=other").status_code == 403
        assert leader.get(status + query).status_code == 403
        assert leader.get(status + "?view=other").status_code == 403
    assert events.count() == 0


def test_automatic_rereads_are_audited_and_marked(http_scenario):
    """An open report's own re-read is audited and marked automatic (#1620).

    By Administrator decision a Response dashboard or Campaign comparison
    view the page made by itself to bring newer figures in records
    ``automatic: true`` in its audit context, so an audit reader can tell it
    from a view the reader asked for, which has no such key. The marker is
    the re-read's own ``X-ParishKit-Read: passive`` header, which can only
    add the mark: every request, whatever header it sends, is audited, and
    no other value marks it.
    """
    setup, browser = http_scenario
    pointer(setup[2])
    # Each page's address and the views it records, with their outcomes in
    # order: the dashboard records its funnel read once, when it ends, as
    # well as the daily chart's started and final views.
    dashboard = reverse("admin:response_dashboard") + "?scope=historical&timezone=UTC"
    pages = {
        dashboard: {
            "participation_viewed": ["started", "succeeded"],
            "response_dashboard_viewed": ["succeeded"],
        },
        reverse("admin:campaign_comparison"): {
            "campaign_comparison_viewed": ["started", "succeeded"],
        },
    }

    def recorded(event_type):
        """``event_type``'s audit contexts not yet checked, oldest first."""
        rows = list(
            AuditContext.objects.filter(event__event_type=event_type)
            .exclude(event__pk__in=seen)
            .order_by("event__created_at", "event__pk")
            .values_list("event__pk", "context")
        )
        seen.update(pk for pk, _ in rows)
        return [context for _, context in rows]

    seen = set()
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        for path, views in pages.items():
            for headers, automatic in (
                # The reader's own view: no marker.
                ({}, False),
                # The page's automatic re-read: audited, and marked.
                ({"HTTP_X_PARISHKIT_READ": "passive"}, True),
                # Any other value neither marks nor skips the audit.
                ({"HTTP_X_PARISHKIT_READ": "skip-audit"}, False),
                ({"HTTP_X_PARISHKIT_READ": ""}, False),
            ):
                response, _ = read(browser, path, **headers)
                assert response.status_code == 200, (path, headers)
                for event_type, outcomes in views.items():
                    view = recorded(event_type)
                    # Every view records its outcomes, whatever the header.
                    assert [context["outcome"] for context in view] == outcomes, (
                        path,
                        headers,
                        event_type,
                    )
                    for context in view:
                        if automatic:
                            assert context["automatic"] is True, (path, headers)
                        else:
                            assert "automatic" not in context, (path, headers)
