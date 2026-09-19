"""Real Admin sessions, CSRF, restricted intake and maintained preparation workers."""

# ruff: noqa: F811 -- imported fixtures are injected by pytest name.

from uuid import uuid4

import pytest
from django.core import signing
from django.db.models import F
from django.test import Client

from parishkit.stewardship.accounts.activation_progress import SALT
from parishkit.stewardship.accounts.authentication import AuthRuntime
from parishkit.stewardship.accounts.go_live_commands import (
    start_cleanup,
    verify_preview,
)
from parishkit.stewardship.accounts.models import PortalSession, PortalUser
from parishkit.stewardship.accounts.setup_completion import setup_is_complete
from parishkit.stewardship.campaigns.activation_models import (
    ProductionTokenCancellation,
    ProductionTokenPreparation,
)
from parishkit.stewardship.campaigns.activation_tasks import token_handler
from parishkit.stewardship.campaigns.activation_tokens import (
    CLEANUP_TASK_TYPE,
    TASK_TYPE,
)
from parishkit.stewardship.campaigns.credential_models import FamilyAccessToken
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.dispatch import execute_hint
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.jobs.storage import _status

from . import test_go_live_cleanup_postgresql as cleanup_tests
from .credential_builders import keys
from .test_background_grants_postgresql import task_login
from .test_cleanup_tasks_postgresql import run
from .test_go_live_cleanup_postgresql import (  # noqa: F401
    bootstrapped,
    config_role,
    ready_cleanup,
    setup_service,
)
from .test_setup_mail_views_postgresql import web_login
from .test_setup_views_postgresql import post
from .test_taskrun_postgresql import act

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def ready_links(request, monkeypatch, settings, real_limiter):
    """Share one actual setup/cleanup across each complete HTTP/worker scenario."""
    rings = []

    def capture_keys():
        """Retain keys only when ordinary setup reaches its key-installation step."""
        ring = keys()
        rings.append(ring)
        return ring

    monkeypatch.setattr(cleanup_tests, "keys", capture_keys)
    login, service, campaign = request.getfixturevalue("ready_cleanup")
    with web_login():
        _, _, token = verify_preview(login, service, campaign.pk)
        status = start_cleanup(
            login, service, campaign.pk, preview_token=token, acknowledge=True
        )
    with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
        assert run(status)
    settings.STEWARDSHIP_AUTH_RUNTIME = AuthRuntime(
        service.store, real_limiter, setup_is_complete
    )
    browser = Client(enforce_csrf_checks=True)
    browser.cookies["pk_admin"] = login.session.session_key
    path = f"/admin/campaign/{campaign.pk}/go-live/cleanup/{status.request_id}/links"
    return browser, path, rings[0], login


def test_admin_prepares_retries_and_discards_without_activating(ready_links):
    """An HTTP command records intent; only the separate maintained worker seals."""
    browser, path, ring, login = ready_links
    with web_login():
        activity = PortalSession.objects.get(
            pk=login.portal_session.pk
        ).last_activity_at
        page = browser.get(path)
        assert page.status_code == 200, page.content
        assert page["Cache-Control"] == "no-store"
        assert page.context["prepare"]
        assert not ProductionTokenPreparation.objects.exists()
        assert (
            PortalSession.objects.get(pk=login.portal_session.pk).last_activity_at
            == activity
        )
        values = {"control": page.context["prepare"]}
        forged = signing.loads(values["control"], salt=SALT)
        forged["inputs"]["source_generation"] += 1
        assert (
            post(
                browser, path, {"control": signing.dumps(forged, salt=SALT)}
            ).status_code
            == 409
        )
        assert browser.post(path, values).status_code == 403
        assert post(browser, path, values | {"actor": "other"}).status_code == 400
        assert post(browser, path, values).status_code == 302
        assert post(browser, path, values).status_code == 302
        assert ProductionTokenPreparation.objects.count() == 1
        preparation = ProductionTokenPreparation.objects.get()
        assert not FamilyAccessToken.objects.exists()
        assert browser.get(path).context["prepare"] is None

    # Simulate one exhausted run using actual task transitions. The HTTP retry
    # must create a child, not reopen this terminal execution or rebind its input.
    failed = act(
        act(_status(TaskRun.objects.get(pk=preparation.task_id)), "claim"),
        "permanent_failure",
    )
    with web_login():
        page = browser.get(path)
        retry = {"control": page.context["records"][0]["controls"]["retry"]}
        assert post(browser, path, retry).status_code == 302
        assert post(browser, path, retry).status_code == 302
        task = TaskRun.objects.filter(root_id=preparation.task_id).latest(
            "retry_sequence"
        )
        assert task.parent_id == failed.run_id
    with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
        assert execute_hint(
            task.pk,
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={TASK_TYPE: token_handler(public=ring.public)},
        )
    with web_login():
        page = browser.get(path)
        assert b"Inactive links are prepared" in page.content
        assert page.context["records"][0]["task"].state == "succeeded"
        cancel = {"control": page.context["records"][0]["controls"]["cancel"]}
        assert post(browser, path, cancel).status_code == 302
        assert post(browser, path, cancel).status_code == 302
        cancellation = ProductionTokenCancellation.objects.get()
    act(
        act(_status(TaskRun.objects.get(pk=cancellation.task_id)), "claim"),
        "permanent_failure",
    )
    with web_login():
        page = browser.get(path)
        retry_disposal = {
            "control": page.context["records"][0]["controls"]["retry_cleanup"]
        }
        assert post(browser, path, retry_disposal).status_code == 302
        assert post(browser, path, retry_disposal).status_code == 302
        cleanup_task = TaskRun.objects.filter(root_id=cancellation.task_id).latest(
            "retry_sequence"
        )
    with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
        assert execute_hint(
            cleanup_task.pk,
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={CLEANUP_TASK_TYPE: token_handler(cleanup=True)},
        )
    assert not FamilyAccessToken.objects.filter(destroyed_at__isnull=True).exists()
    with web_login():
        page = browser.get(path)
        assert page.status_code == 200, page.content
        assert not page.context["records"][0]["current"]
        assert page.context["records"][0]["cleanup"].state == "succeeded"
    # Neither a retained cookie nor a previously signed control survives revocation.
    PortalUser.objects.update(disabled=True, version=F("version") + 1)
    with web_login():
        assert browser.get(path).status_code == 403
        assert post(browser, path, values).status_code == 403
    assert ProductionTokenPreparation.objects.count() == 1
