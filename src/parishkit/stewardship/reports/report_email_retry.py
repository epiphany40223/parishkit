"""Explicit Admin retries retain the same preparation or metadata-finalizer root.

A report send's (#1786) preparation or its metadata finalizer; the Daily and
Weekly digests', which report emails replaced, are all settled.
"""

from uuid import UUID

from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.jobs.delivery_admin import authorize
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.storage import _status, retry_failed
from parishkit.stewardship.storage import StaleRecordError

from .report_email_finalization import TASK_TYPE as FINALIZE
from .report_email_finalization import admit_finalization
from .report_email_ownership import TASK_TYPE as PREPARE
from .report_email_tasks import admit_report

TASK_TYPES = (PREPARE, FINALIZE)
_ADMISSION = {PREPARE: admit_report, FINALIZE: admit_finalization}


def retry_report(store, user_id, task_id, *, command_id):
    """Retry the selected latest failed run, rechecking authority even on replay."""
    if any(not isinstance(value, UUID) for value in (user_id, task_id, command_id)):
        raise ValueError("Report retries require canonical identities.")
    with work_transaction():
        authorize(store, user_id)
        task = TaskRun.objects.get(pk=task_id, task_type__in=TASK_TYPES)
        runs = TaskRun.objects.filter(root_id=task.root_id)
        previous = runs.filter(retry_command_id=command_id).first()
        if previous is not None:
            if previous.parent_id != task_id:
                raise StaleRecordError("Report retry command selects a different run.")
            if previous.initiated_by_id != user_id:
                raise ValueError("Report retry command is already bound.")
            return _status(previous)
        if runs.order_by("-retry_sequence").first().pk != task_id:
            raise StaleRecordError("This report task has a newer execution.")
        return retry_failed(
            run_id=task_id,
            command_id=command_id,
            actor_id=user_id,
            correlation_id=command_id,
            admit=_ADMISSION[task.task_type],
        )
