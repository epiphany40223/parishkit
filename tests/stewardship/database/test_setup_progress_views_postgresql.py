"""Progress HTTP cannot renew unrelated, invisible or uncorrelated setup work."""

from uuid import uuid4

import pytest

from parishkit.stewardship.accounts.models import PortalSession
from parishkit.stewardship.accounts.setup_models import SetupAttempt

from .test_bootstrap_postgresql import bootstrapped  # noqa: F401
from .test_runtime_auth_grants_postgresql import web_login
from .test_setup_progress_postgresql import aged_original_load, bind_load
from .test_setup_views_postgresql import post, setup_http, started  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def progress_browser(request):
    """Use real browser login and a synthetic provider task with actual SQL leases."""
    request.getfixturevalue("setup_http")
    request.getfixturevalue("google")
    browser = started()
    task, _ = bind_load(SetupAttempt.objects.get().pk)
    aged_original_load(task.run_id, minutes=6)
    return browser, task, f"/admin/setup/source/{task.run_id}"


def test_progress_get_is_passive_and_csrf_post_renews_only_the_bound_load(
    progress_browser,
):
    """The exact page displays all deadlines and the real load-duration expectation."""
    browser, task, path = progress_browser
    before = PortalSession.objects.get().last_activity_at
    with web_login():
        page = browser.get(path)
        assert page.status_code == 200
        assert b"two to three minutes" in page.content
        assert b"two-hour watchdog" in page.content
        assert page["Cache-Control"] == "no-store"
        passive = browser.get(path + "?format=json")
        assert passive.status_code == 200 and not passive.json()["renewed"]
        assert PortalSession.objects.get().last_activity_at == before
        assert browser.post(path + "?format=json").status_code == 403
        renewed = post(browser, path + "?format=json", {})
        assert renewed.status_code == 200, renewed.content
        assert renewed.json()["renewed"]
        assert renewed.json()["task_id"] == str(task.run_id)
        assert not post(browser, path + "?format=json", {}).json()["renewed"]
        # Progressive enhancement is optional: a manual form POST returns HTML.
        assert post(browser, path, {})["Content-Type"].startswith("text/html")


def test_progress_cannot_accept_browser_deadlines_or_unbound_tasks(progress_browser):
    """Hidden fields do not become heartbeat evidence or provider authority."""
    browser, _, path = progress_browser
    with web_login():
        for values in (
            {"worker_live": "true"},
            {"heartbeat": "now"},
            {"task_id": str(uuid4())},
        ):
            assert post(browser, path + "?format=json", values).status_code == 400
        assert browser.get(path + "?format=private").status_code == 400
        assert browser.get(f"/admin/setup/source/{uuid4()}").status_code == 404
        assert SetupAttempt.objects.get().renewed_at is None


def test_watchdog_response_stops_polling_and_fences_setup(progress_browser):
    """Even a live source lease cannot buy another second past the original bound."""
    browser, task, path = progress_browser
    aged_original_load(task.run_id, minutes=120)
    with web_login():
        response = post(browser, path + "?format=json", {})
        assert response.status_code == 200
        assert not response.json()["active"]
        assert not response.json()["renewed"]
        assert response.json()["setup_state"] == "expired"
        assert SetupAttempt.objects.get().expiry_reason == "watchdog"
