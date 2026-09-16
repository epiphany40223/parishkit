"""Real signed-login, CSRF, requester API and guarded streaming integration."""

import socket
from contextlib import contextmanager
from copy import deepcopy
from uuid import uuid4

import pytest
from django.db import connection
from django.test import Client
from psycopg import sql

from parishkit.stewardship.accounts.auth_incidents import record_incident
from parishkit.stewardship.accounts.authentication import AuthRuntime
from parishkit.stewardship.accounts.limiting import Limiter
from parishkit.stewardship.campaigns.read_guards import DownloadPool, ReadLimits
from parishkit.stewardship.reports.export_models import ExportRequest

from .auth_builders import signed_in, valkey_client
from .test_background_grants_postgresql import task_login
from .test_export_jobs_postgresql import run_export, scenario  # noqa: F401
from .test_runtime_auth_grants_postgresql import web_login

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def http_scenario(scenario, google, settings):  # noqa: F811
    """Keep the real local limiter and signed OAuth, replacing only provider I/O."""
    client = valkey_client()
    namespace = "parishkit-test:" + uuid4().hex
    limiter = Limiter(
        client,
        b"synthetic-report-limiter-key-material",
        namespace=namespace,
        incident=record_incident,
    )
    settings.STEWARDSHIP_AUTH_RUNTIME = AuthRuntime(scenario[0], limiter, lambda: True)
    settings.SOCIALACCOUNT_PROVIDERS = {
        "google": {
            "OAUTH_PKCE_ENABLED": True,
            "APPS": [
                {
                    "client_id": "synthetic-client",
                    "secret": "synthetic-secret",
                    "key": "",
                }
            ],
        }
    }
    settings.STEWARDSHIP_REPORTS_ROOT = scenario[3]
    settings.STEWARDSHIP_DOWNLOAD_POOL = DownloadPool(ReadLimits(process_pool_size=1))
    try:
        browser, response = signed_in()
        assert response.status_code == 302
        yield scenario, browser
    finally:
        keys = list(client.scan_iter(namespace + ":*"))
        if keys:
            client.delete(*keys)
        client.close()


def post(browser, url, data=None, **extra):
    """API requests carry CSRF in its standard header, never a query string."""
    return browser.post(
        url, data or {}, HTTP_X_CSRFTOKEN=browser.cookies["csrftoken"].value, **extra
    )


def create(http_scenario, *, format="csv"):
    """Select a complete pinned generation through the public requester endpoint."""
    setup, browser = http_scenario
    facts = setup[2]
    response = post(
        browser,
        f"/admin/campaign/{facts.campaign_id}/exports/participation",
        {
            "fact_set_id": str(facts.pk),
            "format": format,
            "browser_timezone": "UTC",
            "request_key": str(uuid4()),
        },
    )
    assert response.status_code == 202
    assert response["Cache-Control"] == "no-store"
    return ExportRequest.objects.get(pk=response.json()["id"])


def test_real_api_requires_csrf_and_rejects_arbitrary_query_inputs(http_scenario):
    """Export creation is never a GET or an arbitrary report/query execution port."""
    setup, browser = http_scenario
    path = f"/admin/campaign/{setup[2].campaign_id}/exports/participation"
    assert browser.get(path).status_code == 405
    assert browser.post(path, {}).status_code == 403
    assert post(browser, path, {"report": "system_logs"}).status_code == 400
    request = create(http_scenario)
    response = browser.get(f"/admin/exports/{request.pk}")
    assert response.status_code == 200
    assert response.json()["state"] == "queued"
    assert "task_id" not in response.json()
    assert post(browser, f"/admin/exports/{request.pk}/cancel").status_code == 200


@contextmanager
def restricted_download_pool(settings):
    """Use distinct real SQL logins for the HTTP web and dedicated read paths."""
    with task_login("download"):
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_user")
            role = cursor.fetchone()[0]
            cursor.execute("RESET SESSION AUTHORIZATION")
            cursor.execute(
                sql.SQL("ALTER ROLE {} PASSWORD {}").format(
                    sql.Identifier(role), sql.Literal("disposable-download-test-only")
                )
            )
        database = deepcopy(connection.settings_dict)
        database.update(USER=role, PASSWORD="disposable-download-test-only")
        settings.STEWARDSHIP_DOWNLOAD_POOL = DownloadPool(
            ReadLimits(process_pool_size=1), database=database
        )
        with web_login():
            yield


@pytest.mark.parametrize("format", ["csv", "png", "pdf"])
def test_complete_download_holds_guard_and_streams_with_private_headers(
    http_scenario, settings, format
):
    """A signed-in user consumes one grant; files never use proxy redirects."""
    setup, browser = http_scenario
    request = create(http_scenario, format=format)
    run_export(setup, request)
    grant = post(browser, f"/admin/exports/{request.pk}/download-grant").json()["grant"]
    server, peer = socket.socketpair()
    try:
        with restricted_download_pool(settings):
            response = post(
                browser,
                "/admin/exports/download",
                {"grant": grant},
                **{"gunicorn.socket": server},
            )
            assert response.status_code == 200
            assert response.streaming
            assert response["Cache-Control"] == "no-store"
            assert (
                response["Content-Type"]
                == {"csv": "text/csv", "png": "image/png", "pdf": "application/pdf"}[
                    format
                ]
            )
            assert "X-Accel-Redirect" not in response
            assert b"".join(response.streaming_content).startswith(
                {"csv": b"date,scope,", "png": b"\x89PNG", "pdf": b"%PDF"}[format]
            )
            response.close()
        assert (
            post(
                browser,
                "/admin/exports/download",
                {"grant": grant},
                **{"gunicorn.socket": server},
            ).status_code
            == 403
        )
    finally:
        server.close()
        peer.close()


def test_download_busy_is_retryable_and_does_not_discard_artifact(
    http_scenario, settings
):
    """A capacity failure consumes its grant but permits a fresh authorized retry."""
    setup, browser = http_scenario
    request = create(http_scenario)
    run_export(setup, request)
    grant = post(browser, f"/admin/exports/{request.pk}/download-grant").json()["grant"]
    pool = settings.STEWARDSHIP_DOWNLOAD_POOL
    server, peer = socket.socketpair()
    pool.acquire()
    try:
        response = post(
            browser,
            "/admin/exports/download",
            {"grant": grant},
            **{"gunicorn.socket": server},
        )
        assert response.status_code == 503
        assert response["Retry-After"] == "5"
    finally:
        pool.release()
        server.close()
        peer.close()
    assert browser.get(f"/admin/exports/{request.pk}").json()["state"] == "ready"
    assert (
        post(browser, f"/admin/exports/{request.pk}/download-grant").status_code == 200
    )


def test_anonymous_requests_never_get_export_metadata(http_scenario):
    """No authenticated requester means no campaign/file existence disclosure."""
    request = create(http_scenario)
    assert Client().get(f"/admin/exports/{request.pk}").status_code in {302, 403}


def test_form_csrf_and_invalid_cancel_are_distinct_from_completed_conflict(
    http_scenario,
):
    """Conventional form CSRF works; malformed commands do not return conflicts."""
    setup, browser = http_scenario
    request = create(http_scenario)
    path = f"/admin/exports/{request.pk}/cancel"
    assert post(browser, path, {"unknown": "value"}).status_code == 400
    run_export(setup, request)
    assert post(browser, path).status_code == 409
    queued = create(http_scenario)
    response = browser.post(
        f"/admin/exports/{queued.pk}/cancel",
        {"csrfmiddlewaretoken": browser.cookies["csrftoken"].value},
    )
    assert response.status_code == 200
