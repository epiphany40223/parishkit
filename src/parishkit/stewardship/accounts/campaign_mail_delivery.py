"""Commit-before-send and bounded uncertainty recovery for campaign readiness mail."""

from django.db import connection
from django.db.models import F

from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.jobs.ownership import lock_task_claim
from parishkit.stewardship.observability import current_correlation
from parishkit.stewardship.readiness_delivery import DeliveryOutcome
from parishkit.stewardship.readiness_mail import ReadinessMail
from parishkit.stewardship.storage import StorageInvariantError

from .campaign_mail import live
from .campaign_mail_models import CampaignMailTest
from .credential_database import _identity
from .delivery_results import settle_result
from .sessions import database_now


def _change(row, state, *, actor_id=None, **values):
    """Use the immutable journal's SQL owner for timestamps and terminal scrubbing."""
    CampaignMailTest.objects.filter(pk=row.pk, version=row.version).update(
        state=state,
        actor_id=actor_id,
        correlation_id=current_correlation(),
        version=F("version") + 1,
        **values,
    )
    row.refresh_from_db()
    return row


def begin_submission(identifier, claim):
    """Only a maintained mail claim can commit the irreversible provider boundary."""
    if connection.in_atomic_block:
        raise StorageInvariantError(
            "Campaign test submission must commit independently."
        )
    _identity("pk_stewardship_mail_dispatch")
    with work_transaction():
        task = lock_task_claim(claim)
        row = CampaignMailTest.objects.select_for_update().get(pk=identifier)
        if (
            row.state != "queued"
            or task.pk != row.task_id
            or task.domain_request_id != row.pk
            or not live(row)
        ):
            raise PermissionError("Campaign test submission is no longer admitted.")
        mail = ReadinessMail.from_payload(row.mail)
        _change(
            row,
            "submitting",
            run_id=claim.run_id,
            task_fence=claim.fence,
            worker_id=claim.worker_id,
            actor_id=claim.worker_id,
            submitted_at=database_now(),
            deadline_at=database_now(),
        )
        return mail, row.deadline_at


def finish_submission(identifier, claim, outcome):
    """Retain observed acceptance even if configuration changed during the call."""
    if not isinstance(outcome, DeliveryOutcome):
        raise ValueError("A closed campaign delivery outcome is required.")
    _identity("pk_stewardship_mail_dispatch")
    with work_transaction():
        lock_task_claim(claim)
        row = CampaignMailTest.objects.select_for_update().get(pk=identifier)
        if (row.state, row.run_id, row.task_fence, row.worker_id) != (
            "submitting",
            claim.run_id,
            claim.fence,
            claim.worker_id,
        ):
            raise PermissionError("Only the original campaign submission can finish.")
        return settle_result(
            outcome,
            deadline=row.deadline_at,
            write=lambda result: _change(
                row, result.value, finished_at=database_now(), actor_id=claim.worker_id
            ),
        )


def recover_pending():
    """Cancel stale unsent tests; retain drained uncertainty without retrying."""
    _identity("pk_stewardship_scheduler")
    with work_transaction():
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT d.id,d.state FROM public.stewardship_campaign_mail_test d "
                "WHERE (d.state='queued' AND (NOT "
                "public.stewardship_campaign_mail_live_v1(d.configuration_id,"
                "d.campaign_id,d.template_id,d.fingerprint,d.requested_by_id) "
                "OR EXISTS (SELECT 1 FROM public.stewardship_task_run original "
                "WHERE original.id=d.task_id "
                "AND original.state IN ('failed','cancelled')))) "
                "OR (d.state='submitting' AND d.deadline_at<=clock_timestamp() "
                "AND NOT EXISTS (SELECT 1 FROM public.stewardship_task_run task "
                "WHERE task.id=d.run_id AND task.state='running' "
                "AND task.fence=d.task_fence AND task.worker_id=d.worker_id "
                "AND task.lease_expires_at>clock_timestamp())) "
                "ORDER BY d.created_at,d.id LIMIT 100 FOR UPDATE OF d"
            )
            found = cursor.fetchall()
        for identifier, state in found:
            row = CampaignMailTest.objects.get(pk=identifier)
            _change(
                row,
                "cancelled" if state == "queued" else "delivery_unknown",
                finished_at=database_now(),
            )
        return len(found)
