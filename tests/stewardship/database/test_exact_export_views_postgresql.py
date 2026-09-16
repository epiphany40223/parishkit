"""Signed-in, CSRF-protected queued-export endpoints expose only requester work."""

from uuid import uuid4

import pytest

from parishkit.stewardship.reports.exact_models import ExactExportRequest

from .test_export_jobs_postgresql import scenario  # noqa: F401
from .test_export_views_postgresql import http_scenario, post  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)


def test_exact_request_status_cancel_and_retry_http_contract(http_scenario):  # noqa: F811
    setup, browser = http_scenario
    path = f"/admin/campaign/{setup[2].campaign_id}/exports/exact-participation"
    values = {
        "population_scope": "current",
        "format": "csv",
        "browser_timezone": "UTC",
        "request_key": str(uuid4()),
    }
    assert browser.get(path).status_code == 405
    assert browser.post(path, values).status_code == 403
    assert post(browser, path, values | {"source_id": str(uuid4())}).status_code == 400
    response = post(browser, path, values)
    assert response.status_code == 202
    assert response["Cache-Control"] == "no-store"
    identifier = response.json()["id"]
    assert post(browser, path, values).json()["id"] == identifier
    status = browser.get(f"/admin/exact-exports/{identifier}")
    assert status.status_code == 200
    assert status.json()["state"] == "queued"
    assert status.json()["export_id"] is None
    assert status.json()["expires_at"] is None
    assert (
        browser.get(f"/admin/exact-exports/{identifier}?sql=private").status_code == 400
    )
    assert (
        post(
            browser,
            f"/admin/exact-exports/{identifier}/retry",
            {"request_key": str(uuid4())},
        ).status_code
        == 409
    )
    assert post(browser, f"/admin/exact-exports/{identifier}/cancel").status_code == 200
    assert (
        browser.get(f"/admin/exact-exports/{identifier}").json()["state"] == "cancelled"
    )
    assert ExactExportRequest.objects.count() == 1
