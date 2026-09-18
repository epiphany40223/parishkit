"""Weekly preparation retries preserve ownership, current scope and Admin intent."""

from uuid import uuid4

import pytest

from parishkit.stewardship.campaigns.schedule_models import ScheduleDefinition
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.reports.digest_retry import retry_digest
from parishkit.stewardship.storage import StaleRecordError

from ..policy_factory import address
from .auth_builders import signed_in
from .campaign_builders import campaign_clock, change
from .test_background_grants_postgresql import task_login
from .test_policy_postgresql import user
from .test_schedule_reconciliation_postgresql import replace_schedule
from .test_taskrun_postgresql import act
from .test_weekly_capture_postgresql import INSTANT
from .test_weekly_tasks_postgresql import execute, queued

pytestmark = pytest.mark.django_db(transaction=True)


def fail_preparation(harness):
    """Fail the genuine claimed owner without changing its retained inputs."""
    return act(act(queued(harness), "claim"), "permanent_failure")


def test_weekly_retry_form_requires_csrf_and_exact_latest_run(response_service, google):
    """The browser posts only an opaque task and idempotent command identifier."""
    browser, _ = signed_in()
    with campaign_clock(INSTANT):
        status = fail_preparation(response_service)
        path = f"/admin/background/tasks/{status.run_id}/retry-weekly-digest"
        page_path = f"/admin/background/task/{status.run_id}"
        values = {"command_id": str(uuid4())}
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            page = browser.get(page_path)
            assert page.status_code == 200 and b"retry-weekly-digest" in page.content
            assert browser.post(path, values).status_code == 403
            for _ in range(2):
                result = browser.post(
                    path, values, HTTP_X_CSRFTOKEN=browser.cookies["csrftoken"].value
                )
                assert result.status_code == 302
            assert b"retry-weekly-digest" not in browser.get(page_path).content
            assert (
                browser.post(
                    path,
                    {"command_id": str(uuid4())},
                    HTTP_X_CSRFTOKEN=browser.cookies["csrftoken"].value,
                ).status_code
                == 409
            )
        assert TaskRun.objects.get(pk=status.run_id).state == "failed"
        assert TaskRun.objects.filter(root_id=status.root_id).count() == 2


def test_weekly_retry_keeps_original_owner_and_finishes(response_service):
    """Worker execution completes the same finite interval, not a fresh report."""
    principal = user("admin@example.org")
    with campaign_clock(INSTANT):
        status = fail_preparation(response_service)
        command = uuid4()
        with task_login(ServiceRole.WEB, exact=True):
            retry = retry_digest(
                response_service.service.store,
                principal.pk,
                status.run_id,
                command_id=command,
            )
        assert retry.domain_request_id == status.domain_request_id
        with task_login(ServiceRole.WORKER, exact=True):
            execute(retry)
        with task_login(ServiceRole.WEB, exact=True):
            replay = retry_digest(
                response_service.service.store,
                principal.pk,
                status.run_id,
                command_id=command,
            )
            assert replay.run_id == retry.run_id and replay.state == "succeeded"
            with pytest.raises(StaleRecordError):
                retry_digest(
                    response_service.service.store,
                    principal.pk,
                    status.run_id,
                    command_id=uuid4(),
                )


@pytest.mark.parametrize("role", ["staff", "ministry_leader"])
def test_non_admin_cannot_retry_weekly_work(response_service, role):
    """Private report work cannot be queued by general report readers."""
    with campaign_clock(INSTANT):
        status = fail_preparation(response_service)
        store = response_service.service.store
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
        principal = user("reader@example.org")
        with task_login(ServiceRole.WEB, exact=True), pytest.raises(PermissionError):
            retry_digest(store, principal.pk, status.run_id, command_id=uuid4())
        assert TaskRun.objects.filter(root_id=status.root_id).count() == 1


def test_replaced_weekly_schedule_cannot_be_retried(response_service):
    """A new schedule revision cannot revive an obsolete private preparation."""
    principal = user("admin@example.org")
    with campaign_clock(INSTANT):
        status = fail_preparation(response_service)
        store = response_service.service.store
        replace_schedule(
            store, ScheduleDefinition.objects.get(kind="weekly_digest"), uuid4()
        )
        with task_login(ServiceRole.WEB, exact=True), pytest.raises(PermissionError):
            retry_digest(store, principal.pk, status.run_id, command_id=uuid4())
        assert TaskRun.objects.filter(root_id=status.root_id).count() == 1
