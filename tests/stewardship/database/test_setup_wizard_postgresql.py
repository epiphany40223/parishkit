"""Wizard-only chrome before initial setup completes, with background reads."""

# ruff: noqa: F811 -- pytest resolves the imported fixture dependencies.

import pytest

from parishkit.stewardship.accounts.setup_models import SetupAttempt

from .test_bootstrap_postgresql import bootstrapped  # noqa: F401
from .test_runtime_auth_grants_postgresql import web_login
from .test_setup_progress_postgresql import bind_load
from .test_setup_views_postgresql import post, setup_http, started  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)


def test_before_completion_admin_pages_route_to_the_wizard_only(setup_http, google):
    """Other Admin pages redirect, and the chrome offers no other destination."""
    with web_login():
        browser = started()
        for path in ("/admin/", "/admin/configuration/integrations", "/admin/users"):
            response = browser.get(path)
            assert response.status_code == 302, path
            assert response["Location"] == "/admin/setup"
        body = browser.get("/admin/setup").content
        assert b"Initial setup" in body
        for other in (b"Parish settings", b'href="/admin/users"', b"Ministry activity"):
            assert other not in body
        assert b"Sign out" in body


def test_background_work_stays_readable_during_setup(setup_http, google):
    """The header's background link shows the setup load instead of bouncing."""
    browser = started()
    task, _ = bind_load(SetupAttempt.objects.get().pk)
    with web_login():
        body = browser.get("/admin/setup").content
        assert b'href="/admin/background"' in body
        page = browser.get("/admin/background")
        assert page.status_code == 200, page.content
        assert b"Initial ParishSoft data load" in page.content
        assert f"/admin/setup/source/{task.run_id}".encode() in page.content
        detail = browser.get(f"/admin/background/task/{task.run_id}")
        assert detail.status_code == 200
        assert b"Initial ParishSoft data load" in detail.content
        assert browser.get("/admin/background/counts").status_code == 200
        # Commands under the same prefix remain closed until setup completes.
        command = post(
            browser, f"/admin/background/tasks/{task.run_id}/retry-export-cleanup", {}
        )
        assert command.status_code == 503
