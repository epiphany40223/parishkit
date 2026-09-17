"""Admin daily-work retries use real sessions, unchanged roots and current policy."""

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
from .test_daily_digest_planning_postgresql import INSTANT
from .test_daily_digest_tasks_postgresql import execute, queued
from .test_family_mail_preparation_postgresql import family_mail  # noqa: F401
from .test_policy_postgresql import user
from .test_schedule_reconciliation_postgresql import replace_schedule
from .test_taskrun_postgresql import act

pytestmark = pytest.mark.django_db(transaction=True)


def fail_preparation(harness):
    """Drain a claimed task without mutating its owned discovery inputs."""
    status = queued(harness)
    return act(act(status, "claim"), "permanent_failure")


def test_retry_form_enforces_csrf_and_exact_latest_run(family_mail, google):  # noqa: F811
    browser, _ = signed_in()
    with campaign_clock(INSTANT):
        status = fail_preparation(family_mail)
        path = f"/admin/background/tasks/{status.run_id}/retry-daily-digest"
        page_path = f"/admin/background/task/{status.run_id}"
        values = {"command_id": str(uuid4())}
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            page = browser.get(page_path)
            assert page.status_code == 200 and b"retry-daily-digest" in page.content
            assert browser.post(path, values).status_code == 403
            for _ in range(2):
                result = browser.post(
                    path, values, HTTP_X_CSRFTOKEN=browser.cookies["csrftoken"].value
                )
                assert result.status_code == 302
            assert b"retry-daily-digest" not in browser.get(page_path).content
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


def test_retry_keeps_original_owner_and_finishes_under_real_worker(family_mail):  # noqa: F811
    from parishkit.stewardship.reports.digest_tasks import daily_handler

    principal = user("admin@example.org")
    with campaign_clock(INSTANT):
        status = fail_preparation(family_mail)
        command = uuid4()
        with task_login(ServiceRole.WEB, exact=True):
            retry = retry_digest(
                family_mail.service.store,
                principal.pk,
                status.run_id,
                command_id=command,
            )
        assert retry.domain_request_id == status.domain_request_id
        with task_login(ServiceRole.WORKER, exact=True):
            execute(retry, daily_handler(public_origin="https://parish.example"))
        with task_login(ServiceRole.WEB, exact=True):
            replay = retry_digest(
                family_mail.service.store,
                principal.pk,
                status.run_id,
                command_id=command,
            )
            assert replay.run_id == retry.run_id and replay.state == "succeeded"
            with pytest.raises(StaleRecordError):
                retry_digest(
                    family_mail.service.store,
                    principal.pk,
                    status.run_id,
                    command_id=uuid4(),
                )


@pytest.mark.parametrize("role", ["staff", "ministry_leader"])
def test_non_admin_cannot_retry_daily_work(family_mail, role):  # noqa: F811
    with campaign_clock(INSTANT):
        status = fail_preparation(family_mail)
        store = family_mail.service.store
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


def test_superseded_revision_is_not_resurrected_by_retry(family_mail):  # noqa: F811
    principal = user("admin@example.org")
    with campaign_clock(INSTANT):
        status = fail_preparation(family_mail)
        store = family_mail.service.store
        replace_schedule(
            store, ScheduleDefinition.objects.get(kind="daily_digest"), uuid4()
        )
        with task_login(ServiceRole.WEB, exact=True), pytest.raises(PermissionError):
            retry_digest(store, principal.pk, status.run_id, command_id=uuid4())
        assert TaskRun.objects.filter(root_id=status.root_id).count() == 1
