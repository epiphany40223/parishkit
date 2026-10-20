"""The Family link preparation owner, its restricted intake and maintained workers.

The Prepare Family links page retired with #1585 slice 3: the go-live
producer prepares and discards the links, and the Go live page's Prepare
step retries them, both through ``activation_progress`` as this file
drives it. The page-level HTTP checks live with the Prepare step
(test_go_live_prepare_postgresql.py).
"""

# ruff: noqa: F811 -- imported fixtures are injected by pytest name.

from dataclasses import dataclass
from uuid import uuid4

import pytest
from django.core import signing
from django.core.signing import BadSignature
from django.db.models import F
from django.test import Client

from parishkit.stewardship.accounts import activation_progress
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
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.contracts import PageWindow
from parishkit.stewardship.web.refusals import UserFacingStale

from . import test_go_live_cleanup_postgresql as cleanup_tests
from .credential_builders import keys
from .preview_builders import previews_aged
from .test_background_grants_postgresql import task_login
from .test_cleanup_tasks_postgresql import run
from .test_go_live_cleanup_postgresql import (  # noqa: F401
    bootstrapped,
    config_role,
    ready_cleanup,
    setup_service,
)
from .test_setup_mail_views_postgresql import web_login
from .test_taskrun_postgresql import act

pytestmark = pytest.mark.django_db(transaction=True)


@dataclass(frozen=True)
class Links:
    """The link preparation owner for one go-live request, as a page calls it."""

    login: object
    service: object
    campaign_id: object
    request_id: object

    def page(self):
        """The owner's passive read: the preparations and their controls."""
        return activation_progress.progress(
            self.login,
            self.service,
            self.campaign_id,
            self.request_id,
            window=PageWindow(1, 25),
        )

    def act(self, token):
        """Apply one of the owner's signed controls."""
        return activation_progress.control(
            self.login, self.service, self.campaign_id, self.request_id, token=token
        )


@pytest.fixture
def ready_links(request, monkeypatch, settings, real_limiter):
    """Share one actual setup/cleanup across each complete owner/worker scenario.

    Returns ``(browser, links, ring, login)``: a signed-in Admin browser for
    the pages that follow, the link owner (``Links``), the public key ring
    and the Admin's login.
    """
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
    links = Links(login, service, campaign.pk, status.request_id)
    return browser, links, rings[0], login


def test_admin_prepares_retries_and_discards_without_activating(
    ready_links, monkeypatch
):
    """A command records intent; only the separate maintained worker seals."""
    _, links, ring, login = ready_links
    with web_login():
        activity = PortalSession.objects.get(
            pk=login.portal_session.pk
        ).last_activity_at
        page = links.page()
        assert page["prepare"]
        assert not ProductionTokenPreparation.objects.exists()
        # The read is passive: it never extends the sign-in's idle time.
        assert (
            PortalSession.objects.get(pk=login.portal_session.pk).last_activity_at
            == activity
        )
        forged = signing.loads(page["prepare"], salt=SALT)
        forged["inputs"]["source_generation"] += 1
        with pytest.raises(StaleRecordError):
            links.act(signing.dumps(forged, salt=SALT))
        # An expired control is the plain out-of-date refusal (#398).
        with previews_aged(monkeypatch), pytest.raises(UserFacingStale):
            links.act(page["prepare"])
        with pytest.raises(BadSignature):
            links.act(page["prepare"] + "x")
        assert not ProductionTokenPreparation.objects.exists()
        links.act(page["prepare"])
        links.act(page["prepare"])
        assert ProductionTokenPreparation.objects.count() == 1
        preparation = ProductionTokenPreparation.objects.get()
        assert not FamilyAccessToken.objects.exists()
        assert links.page()["prepare"] is None

    # Simulate one exhausted run using actual task transitions. The retry
    # must create a child, not reopen this terminal execution or rebind its input.
    failed = act(
        act(_status(TaskRun.objects.get(pk=preparation.task_id)), "claim"),
        "permanent_failure",
    )
    with web_login():
        retry = links.page()["records"][0]["controls"]["retry"]
        other_retry = links.page()["records"][0]["controls"]["retry"]
        links.act(retry)
        links.act(retry)
        with pytest.raises(StaleRecordError):
            links.act(other_retry)
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
        page = links.page()
        assert page["records"][0]["task"].state == "succeeded"
        assert page["records"][0]["generation"].state == "ready"
        assert page["records"][0]["current"]
        cancel = page["records"][0]["controls"]["cancel"]
        other_cancel = links.page()["records"][0]["controls"]["cancel"]
        links.act(cancel)
        links.act(cancel)
        with pytest.raises(StaleRecordError):
            links.act(other_cancel)
        cancellation = ProductionTokenCancellation.objects.get()
    act(
        act(_status(TaskRun.objects.get(pk=cancellation.task_id)), "claim"),
        "permanent_failure",
    )
    with web_login():
        retry_disposal = links.page()["records"][0]["controls"]["retry_cleanup"]
        links.act(retry_disposal)
        links.act(retry_disposal)
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
        page = links.page()
        assert not page["records"][0]["current"]
        assert page["records"][0]["cleanup"].state == "succeeded"
    # A previously signed control does not survive revocation.
    PortalUser.objects.update(disabled=True, version=F("version") + 1)
    with web_login(), pytest.raises(PermissionError):
        links.act(retry)
    assert ProductionTokenPreparation.objects.count() == 1


def test_new_preparation_fences_old_failed_retry_in_http_and_sql(ready_links):
    """A new root and a retained old retry control cannot create competing work."""
    from django.db import IntegrityError

    from parishkit.stewardship.campaigns.work_locks import work_transaction
    from parishkit.stewardship.jobs.storage import retry_failed

    _, links, _, login = ready_links
    with web_login():
        links.act(links.page()["prepare"])
        preparation = ProductionTokenPreparation.objects.get()
    failed = act(
        act(_status(TaskRun.objects.get(pk=preparation.task_id)), "claim"),
        "permanent_failure",
    )
    with web_login():
        page = links.page()
        retry = page["records"][0]["controls"]["retry"]
        links.act(page["prepare"])
        with pytest.raises(StaleRecordError):
            links.act(retry)
        rows = links.page()["records"]
        old = next(row for row in rows if row["preparation"].pk == preparation.pk)
        assert "retry" not in old["controls"]
        with (
            pytest.raises(IntegrityError, match="retry inputs changed"),
            work_transaction(),
        ):
            retry_failed(
                run_id=failed.run_id,
                command_id=uuid4(),
                actor_id=login.portal_session.principal_id,
                correlation_id=uuid4(),
                admit=lambda *args: True,
            )
    assert TaskRun.objects.filter(root_id=preparation.task_id).count() == 1
