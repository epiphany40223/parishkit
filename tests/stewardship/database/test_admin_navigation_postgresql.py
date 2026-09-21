"""Capability-filtered Admin navigation, operational banners and passive work views."""

from uuid import uuid4

import pytest
from django.db import connection
from django.db.models import F
from django.test import Client

from parishkit.stewardship.accounts.models import PortalSession, PortalUser
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.audit.services import operational
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.phases import TaskPhase
from parishkit.stewardship.observability import Event

from ..policy_factory import address
from .auth_builders import signed_in
from .campaign_builders import add_draft, change
from .test_background_grants_postgresql import task_login
from .test_taskrun_postgresql import act, new

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("role", ["administrator", "staff", "ministry_leader"])
def test_navigation_and_testing_banner_match_current_capabilities(
    auth_service, google, role
):
    """Ministry leaders never receive code links or Admin-only configuration details."""
    store = auth_service.store
    add_draft(store, store.active(), uuid4())
    if role != "administrator":
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
    response = browser.get("/admin/")
    assert response.status_code == 200
    body = response.content
    assert b"Testing mode" in body
    assert (b"test@example.org" in body) == (role == "administrator")
    assert (b"Ministry activity" in body) == (role == "administrator")
    # Who else holds access is offered to Administrators only.
    assert (b'href="/admin/users"' in body) == (role == "administrator")
    assert (b"Parish settings" in body) == (role == "administrator")
    assert (b"Background work" in body) == (role == "administrator")
    assert (b"Family codes" in body) == (role != "ministry_leader")
    assert body.count(b'id="session-warning"') == 1
    assert body.count(b'id="session-expired"') == 1
    assert (b"Family participation" in body) == (role != "ministry_leader")
    assert AuditEvent.objects.filter(event_type="dashboard_viewed").count() == 1


def test_anonymous_and_family_pages_do_not_gain_admin_chrome(auth_service, google):
    """Rendering public/login/error content never derives a menu from runtime alone."""
    for path in ("/", "/admin/login"):
        response = Client().get(path)
        assert b"Ministry activity" not in response.content
        assert b"test@example.org" not in response.content


def test_background_html_shows_exact_progress_and_does_not_renew_idle(
    auth_service, google
):
    """Status shares API authorization and uses exact numeric formatting."""
    task = act(new(), "claim")
    act(task, "progress", progress=(1000, 3000), phase=TaskPhase.FETCHING)
    browser, _ = signed_in()
    activity = PortalSession.objects.get().last_activity_at
    response = browser.get("/admin/background")
    assert response.status_code == 200
    assert b"1,000 out of 3,000 (33.3%)" in response.content
    assert b"data-local-instant" in response.content
    assert PortalSession.objects.get().last_activity_at == activity
    assert response["Cache-Control"] == "no-store"


@pytest.mark.parametrize("role", ["staff", "ministry_leader"])
def test_background_html_is_not_accessible_through_direct_non_admin_url(
    auth_service, google, role
):
    """The visible menu is not the security boundary."""
    store = auth_service.store
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
    assert browser.get("/admin/background").status_code == 403


def test_background_pagination_preserves_filtered_scope(auth_service, google):
    """Following a page link must not unexpectedly broaden a completed-task view."""
    for _ in range(2):
        act(act(new(), "claim"), "complete")
    browser, _ = signed_in()
    response = browser.get("/admin/background?state=succeeded&size=1")
    assert response.status_code == 200
    assert b"state=succeeded&amp;size=1&amp;page=2" in response.content
    assert b'value="succeeded" selected' in response.content


def test_dashboard_failure_links_open_the_html_task_page(auth_service, google):
    """A dashboard navigation link must not strand the Admin on a JSON response."""
    task = act(act(new(), "claim"), "permanent_failure")
    browser, _ = signed_in()
    with task_login(ServiceRole.WEB):
        response = browser.get("/admin/")
        assert response.status_code == 200
        path = f"/admin/background/task/{task.run_id}"
        assert f'href="{path}"'.encode() in response.content
        detail = browser.get(path)
        assert detail.status_code == 200
        assert detail["Content-Type"].startswith("text/html")


@pytest.mark.parametrize("revoke", [False, True])
def test_dashboard_render_releases_work_lock_and_rechecks_authority(
    auth_service, google, monkeypatch, revoke
):
    """Template work cannot serialize task claims or bypass a concurrent revocation."""
    from parishkit.stewardship.accounts import authentication

    browser, _ = signed_in()
    original = authentication.render
    observed = []

    def render(request, *args, **kwargs):
        """Probe the actual backend before rendering the captured observation."""
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT EXISTS(SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid() "
                "AND locktype='advisory' AND classid=736220 AND objid=1 "
                "AND objsubid=2 AND granted)"
            )
            assert cursor.fetchone()[0] is False
        observed.append(True)
        result = original(request, *args, **kwargs)
        if revoke:
            PortalUser.objects.update(disabled=True, version=F("version") + 1)
        return result

    monkeypatch.setattr(authentication, "render", render)
    response = browser.get("/admin/")
    assert response.status_code == (403 if revoke else 200)
    assert observed == [True]
    assert AuditEvent.objects.filter(event_type="dashboard_viewed").count() == (
        0 if revoke else 1
    )
    if revoke:
        assert b"test@example.org" not in response.content


def test_restricted_web_role_can_read_dashboard_and_status_pages(auth_service, google):
    """Dashboard reads use the same real grants as container startup admission."""
    browser, _ = signed_in()
    with task_login(ServiceRole.WEB):
        assert browser.get("/admin/").status_code == 200
        assert browser.get("/admin/background").status_code == 200


@pytest.mark.parametrize("error", [ValueError, TypeError, LookupError])
def test_unexpected_dashboard_failures_use_the_global_closed_error_boundary(
    auth_service, google, monkeypatch, error
):
    """Programming errors remain 500, but never expose private exception values."""
    from parishkit.stewardship.accounts import admin_dashboard

    browser, _ = signed_in()
    browser.raise_request_exception = False

    def failed(*args):
        """Simulate a defect, not known retryable configuration unavailability."""
        raise error("PRIVATE-DASHBOARD-FAILURE")

    monkeypatch.setattr(admin_dashboard, "summary", failed)
    response = browser.get("/admin/")
    assert response.status_code == 500
    assert response.content == b"The request could not be completed.\n"
    assert b"PRIVATE-DASHBOARD-FAILURE" not in response.content
    assert b"test@example.org" not in response.content


def test_critical_event_warning_is_persistent_and_admin_only(auth_service, google):
    """The warning reports recent critical events without exposing their context."""
    operational(Event.TASK_FAILED, level="CRITICAL")
    browser, _ = signed_in()
    for path in ("/admin/", "/admin/background", "/admin/configuration/parish"):
        response = browser.get(path)
        assert b"Critical events recorded in the past 24 hours:" in response.content
    store = auth_service.store
    change(
        store,
        store.active(),
        uuid4(),
        [
            {
                "operation": "add",
                "section": "login_rules",
                **address("reader@example.org", roles=("staff",)),
            }
        ],
    )
    google[0]["email"] = "reader@example.org"
    browser, _ = signed_in()
    assert b"Critical events recorded" not in browser.get("/admin/").content
