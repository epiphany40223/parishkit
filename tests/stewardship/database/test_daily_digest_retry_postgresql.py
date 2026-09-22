"""Admin daily-work retries use real sessions, unchanged roots and current policy."""

from uuid import uuid4

import pytest

from parishkit.stewardship.campaigns.schedule_models import ScheduleDefinition
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.outbox_models import OutboxMessage
from parishkit.stewardship.jobs.ownership import TaskClaim
from parishkit.stewardship.reports.digest_models import DailyDigestPreparation
from parishkit.stewardship.reports.digest_planning import discover_dates
from parishkit.stewardship.reports.digest_retry import retry_digest
from parishkit.stewardship.reports.digest_tasks import daily_handler
from parishkit.stewardship.storage import StaleRecordError

from ..policy_factory import address
from .auth_builders import signed_in
from .campaign_builders import campaign_clock, change
from .test_background_grants_postgresql import task_login
from .test_daily_digest_planning_postgresql import INSTANT, allocate
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
                    path,
                    values,
                    HTTP_X_CSRFTOKEN=browser.cookies["pk_admin_csrf"].value,
                )
                assert result.status_code == 302
            assert b"retry-daily-digest" not in browser.get(page_path).content
            retry = TaskRun.objects.get(parent_id=status.run_id)
            assert (
                browser.post(
                    f"/admin/background/tasks/{retry.pk}/retry-daily-digest",
                    values,
                    HTTP_X_CSRFTOKEN=browser.cookies["pk_admin_csrf"].value,
                ).status_code
                == 409
            )
            assert (
                browser.post(
                    path,
                    {"command_id": str(uuid4())},
                    HTTP_X_CSRFTOKEN=browser.cookies["pk_admin_csrf"].value,
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


@pytest.mark.parametrize("phase", ["dates", "cover"])
def test_unselected_failed_root_retry_after_newer_root_cannot_duplicate_mail(
    family_mail,  # noqa: F811
    phase,
):
    """New work owns the dates; retrying a prior unselected root finishes empty."""
    principal = user("admin@example.org")
    with campaign_clock(INSTANT):
        status = act(queued(family_mail), "claim")
        if phase == "cover":
            with task_login(ServiceRole.WORKER, exact=True), work_transaction():
                discover_dates(TaskClaim(status.run_id, status.fence, status.worker_id))
        failed = act(status, "permanent_failure")
        later = allocate(claim_task=False)
        handler = daily_handler(public_origin="https://parish.example")
        with task_login(ServiceRole.WORKER, exact=True):
            execute(later, handler)
        messages = list(OutboxMessage.objects.values_list("pk", flat=True))
        assert len(messages) == 1
        with task_login(ServiceRole.WEB, exact=True):
            retry = retry_digest(
                family_mail.service.store,
                principal.pk,
                failed.run_id,
                command_id=uuid4(),
            )
        with task_login(ServiceRole.WORKER, exact=True):
            execute(retry, handler)
        assert TaskRun.objects.get(pk=retry.run_id).state == "succeeded"
        original = DailyDigestPreparation.objects.get(task_id=failed.root_id)
        assert original.phase == "complete" and original.occurrence_id is None
        assert list(OutboxMessage.objects.values_list("pk", flat=True)) == messages


@pytest.mark.parametrize("wrong_parent", [False, True])
def test_other_current_admin_cannot_adopt_a_retry_command(family_mail, wrong_parent):  # noqa: F811
    """Actor binding survives replay; parent conflicts keep precedence as stale."""
    first, second = user("admin@example.org"), user("second@example.org")
    store = family_mail.service.store
    with campaign_clock(INSTANT):
        status = fail_preparation(family_mail)
        change(
            store,
            store.active(),
            uuid4(),
            [
                {
                    "operation": "add",
                    "section": "login_rules",
                    **address("second@example.org", roles=("administrator",)),
                }
            ],
        )
        command = uuid4()
        with task_login(ServiceRole.WEB, exact=True):
            retry = retry_digest(store, first.pk, status.run_id, command_id=command)
            with pytest.raises(
                StaleRecordError if wrong_parent else ValueError,
                match="different run" if wrong_parent else "already bound",
            ):
                retry_digest(
                    store,
                    second.pk,
                    retry.run_id if wrong_parent else status.run_id,
                    command_id=command,
                )
        assert TaskRun.objects.filter(root_id=status.root_id).count() == 2
