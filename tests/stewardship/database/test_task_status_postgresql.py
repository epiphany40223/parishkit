"""Real Google/session policy protects operational task metadata and passive polls."""

from uuid import uuid4

import pytest
from django.test import Client

from parishkit.stewardship.accounts.models import PortalSession
from parishkit.stewardship.audit.models import AuditContext, AuditEvent
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs import views
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.phases import TaskPhase

from ..policy_factory import address
from .auth_builders import signed_in
from .campaign_builders import change
from .test_background_grants_postgresql import task_login
from .test_taskrun_postgresql import act, new

pytestmark = pytest.mark.django_db(transaction=True)
BASE = "/admin/background/tasks"


@pytest.mark.parametrize("detail", [False, True, "counts"])
@pytest.mark.parametrize(
    "role,status", [("administrator", 200), ("staff", 403), ("ministry_leader", 403)]
)
def test_only_current_admin_sees_operational_tasks(
    auth_service, google, role, status, detail
):
    """Owning a report/export capability does not expose refresh or other tasks."""
    store = auth_service.store
    if role != "administrator":
        row = address("reader@example.org", roles=(role,))
        assert (
            change(
                store,
                store.active(),
                store.active().version_id,
                [{"operation": "add", "section": "login_rules", **row}],
            ).state
            == "applied"
        )
        google[0]["email"] = "reader@example.org"
    task = new()
    browser, signed = signed_in()
    assert signed.status_code == 302
    path = (
        "/admin/background/counts"
        if detail == "counts"
        else BASE + (f"/{task.run_id}" if detail else "")
    )
    with task_login(ServiceRole.WEB):
        response = browser.get(path)
    assert response.status_code == status
    assert response["Cache-Control"] == "no-store"
    if detail == "counts":
        if status == 200:
            assert set(response.json()) == {"as_of", "counts"}
            assert response.json()["counts"]["queued"] == 1
        assert not AuditEvent.objects.filter(event_type="background_viewed").exists()
        return
    if status == 200:
        row = response.json()["task"] if detail else response.json()["tasks"][0]
        assert row["id"] == str(task.run_id)
        assert set(row) == {
            "id",
            "root_id",
            "parent_id",
            "retry_sequence",
            "type",
            "state",
            "action",
            "version",
            "attempt",
            "initiator_id",
            "progress",
            "created_at",
            "updated_at",
            "not_before",
            "heartbeat_at",
            "lease_expires_at",
            "active",
        }
        assert row["created_at"].endswith("Z")
        assert AuditContext.objects.filter(
            event__event_type="background_viewed"
        ).get().context == {
            "outcome": "succeeded",
            "count": 1,
        }
    else:
        assert str(task.run_id).encode() not in response.content
        assert not AuditEvent.objects.filter(event_type="background_viewed").exists()


def test_anonymous_requests_cannot_distinguish_existing_task(auth_service, google):
    """A UUID is neither authentication nor an operational read capability."""
    task, browser = new(), Client()
    assert browser.get(f"{BASE}/{task.run_id}").status_code == 403
    assert browser.get(f"{BASE}/{uuid4()}").status_code == 403


@pytest.mark.parametrize(
    "query",
    [
        "?page=0",
        "?size=101",
        "?page=10001",
        "?size=2&size=3",
        "?state=private-value",
        "?task_type=private-value",
        "?unknown=private-value",
    ],
)
def test_invalid_status_filters_are_bounded_and_not_reflected(
    auth_service, google, query
):
    """Validation cannot create expensive unbounded reads or echo search content."""
    browser, _ = signed_in()
    response = browser.get(BASE + query)
    assert response.status_code == 400 and b"private-value" not in response.content
    assert not AuditEvent.objects.filter(event_type="background_viewed").exists()


def test_polls_do_not_renew_idle_session_and_report_typed_progress(
    auth_service, google
):
    """Automatic progress/presence requests cannot keep an abandoned Admin signed in."""
    task = act(new(), "claim")
    task = act(task, "progress", progress=(1, 4), phase=TaskPhase.FETCHING)
    browser, _ = signed_in()
    before = PortalSession.objects.get().last_activity_at
    listed = browser.get(BASE).json()
    detailed = browser.get(f"{BASE}/{task.run_id}").json()
    assert PortalSession.objects.get().last_activity_at == before
    assert listed["counts"]["active"] == listed["counts"]["running"] == 1
    assert detailed["task"]["progress"] == {
        "phase": "fetching",
        "current": 1,
        "total": 4,
        "percent": 25.0,
    }
    assert detailed["events"][0]["progress"] == detailed["task"]["progress"]
    audit_count = AuditEvent.objects.count()
    for _ in range(3):
        assert (
            browser.get("/admin/background/counts").json()["counts"] == listed["counts"]
        )
    assert AuditEvent.objects.count() == audit_count
    assert PortalSession.objects.get().last_activity_at == before
    assert browser.get("/admin/background/counts?size=1").status_code == 400


def test_task_and_history_pages_are_bounded_and_terminal_filter_is_explicit(
    auth_service, google
):
    """Both result kinds fetch only one bounded page, with deterministic ordering."""
    first, second = new(), new()
    terminal = act(act(new(), "claim"), "complete")
    browser, _ = signed_in()
    listing = browser.get(BASE, {"size": 1}).json()
    assert listing["has_next"] and listing["tasks"][0]["id"] == str(second.run_id)
    assert browser.get(BASE, {"size": 1, "page": 2}).json()["tasks"][0]["id"] == str(
        first.run_id
    )
    assert browser.get(BASE, {"state": "succeeded"}).json()["tasks"][0]["id"] == str(
        terminal.run_id
    )
    history = browser.get(f"{BASE}/{terminal.run_id}", {"size": 1}).json()
    assert history["has_next"] and history["events"][0]["version"] == terminal.version
    assert len(history["events"]) == 1


@pytest.mark.parametrize("counts_only", [False, True])
def test_revocation_during_query_discards_prepared_metadata(
    auth_service, google, monkeypatch, counts_only
):
    """Fresh response-boundary authorization may deny an initially authorized read."""
    task = new()
    browser, _ = signed_in()
    original, calls = views.authenticated_admin, []

    def authorize(request, **kwargs):
        """Model authorization loss only at the second, post-query check."""
        calls.append(kwargs)
        return None if kwargs.get("read_only") else original(request, **kwargs)

    monkeypatch.setattr(views, "authenticated_admin", authorize)
    response = browser.get(
        "/admin/background/counts" if counts_only else f"{BASE}/{task.run_id}"
    )
    assert response.status_code == 403 and len(calls) == 2
    assert str(task.run_id).encode() not in response.content
    assert not AuditEvent.objects.filter(event_type="background_viewed").exists()


def test_unavailable_status_has_static_retry_error(auth_service, google, monkeypatch):
    """A provider-shaped exception or failed serializer never becomes API content."""
    browser, _ = signed_in()

    def unavailable(*args):
        """The metadata boundary suppresses private values from internal failures."""
        raise ValueError("private-server-value")

    monkeypatch.setattr(views, "_listing", unavailable)
    response = browser.get(BASE)
    assert response.status_code == 503 and response["Retry-After"] == "5"
    assert b"private-server-value" not in response.content
    assert not AuditEvent.objects.filter(event_type="background_viewed").exists()


def test_detail_missing_identity_and_unsupported_methods_do_not_change_tasks(
    auth_service, google
):
    """The API exposes no accidental claim/retry/cancel command through POST."""
    browser, _ = signed_in()
    assert browser.get(f"{BASE}/{uuid4()}").status_code == 404
    response = browser.post(BASE, HTTP_X_CSRFTOKEN=browser.cookies["csrftoken"].value)
    assert response.status_code == 405
    assert not TaskRun.objects.exists()


def test_task_html_detail_is_bounded_passive_and_preserves_history_filters(
    auth_service, google
):
    """The rendered task page uses exactly the authenticated API's closed metadata."""
    task = act(
        act(new(), "claim"), "progress", progress=(1000, 4000), phase=TaskPhase.FETCHING
    )
    browser, _ = signed_in()
    before = PortalSession.objects.get().last_activity_at
    path = f"/admin/background/task/{task.run_id}"
    response = browser.get(path, {"page": 1, "size": 1})
    assert response.status_code == 200 and response["Cache-Control"] == "no-store"
    assert b"1,000 out of 4,000 (25%)" in response.content
    assert b"Task history (newest first)" in response.content
    assert b"page=2&amp;size=1" in response.content
    assert PortalSession.objects.get().last_activity_at == before
    assert browser.get(f"/admin/background/task/{uuid4()}").status_code == 404
    assert Client().get(path).status_code == 403


@pytest.mark.parametrize("detail", [False, True])
@pytest.mark.parametrize("failure", ["render", "revoke"])
def test_html_render_or_final_revocation_cannot_record_success(
    auth_service, google, monkeypatch, detail, failure
):
    """A JSON intermediate is not proof that an HTML report was displayed."""
    task = new()
    browser, _ = signed_in()
    original = views.render

    def render(*args, **kwargs):
        """Lose rendering or the actual login after metadata was selected."""
        assert not AuditEvent.objects.filter(event_type="background_viewed").exists()
        if failure == "render":
            raise RuntimeError("Synthetic render failure")
        result = original(*args, **kwargs)
        from parishkit.stewardship.accounts.sessions import end_admin

        end_admin(args[0])
        return result

    monkeypatch.setattr(views, "render", render)
    path = f"/admin/background/task/{task.run_id}" if detail else "/admin/background"
    if failure == "render":
        with pytest.raises(RuntimeError, match="Synthetic"):
            browser.get(path)
    else:
        response = browser.get(path)
        assert response.status_code == 403
        assert str(task.run_id).encode() not in response.content
    assert not AuditEvent.objects.filter(event_type="background_viewed").exists()
