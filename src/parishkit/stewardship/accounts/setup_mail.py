"""Explicit original-Admin test-mail intake; no provider IO or setup activation."""

from dataclasses import dataclass
from uuid import UUID, uuid5

from django.db import connection
from django.db.models import F

from parishkit.stewardship.campaigns.work_locks import (
    require_work_order,
    work_transaction,
)
from parishkit.stewardship.jobs.ownership import lock_task_claim
from parishkit.stewardship.jobs.storage import enqueue
from parishkit.stewardship.observability import current_correlation
from parishkit.stewardship.readiness_delivery import DeliveryOutcome
from parishkit.stewardship.readiness_mail import ReadinessMail
from parishkit.stewardship.storage import StaleRecordError, StorageInvariantError

from .content_forms import EMAIL_LABELS, sample_render
from .credential_database import _identity
from .delivery_results import settle_result
from .sessions import authenticated_admin, database_now
from .setup_delivery_models import SetupMailDelivery
from .setup_preview import verify_preview
from .setup_secret_models import SetupSealedCredential

TASK_TYPE = "setup_mail_test"


@dataclass(frozen=True)
class SetupMailStatus:
    """The browser needs closed outcome metadata, not stored message content."""

    identifier: UUID
    task_id: UUID
    state: str
    version: int


def _status(row):
    """Detach safe identifiers and state from the temporary rendered payload."""
    return SetupMailStatus(row.pk, row.task_id, row.state, row.version)


def live(row):
    """Repeat the original login, draft, credential and Testing routing SQL proof."""
    require_work_order()
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT public.stewardship_setup_mail_live_v1(%s,%s,%s,%s,%s)",
            [
                row.attempt_id,
                row.attempt_version,
                row.credential_id,
                row.credential_version,
                row.fingerprint,
            ],
        )
        return cursor.fetchone() == (True,)


def request_sample(
    request,
    service,
    *,
    preview_token,
    request_key,
    slot="initial",
    acknowledge_unknown=False,
):
    """Atomically store one explicitly requested sample and its durable Task root.

    The selected sample and recipient come from the current signed preview, not
    HTTP body fields. Repeating the same command cannot allocate another send.
    Any prior unknown send must be explicitly acknowledged by a later UI before
    it calls this service with a different command key; there is no retry here.
    """
    if (
        not isinstance(request_key, UUID)
        or type(slot) is not str
        or slot not in EMAIL_LABELS
        or type(acknowledge_unknown) is not bool
    ):
        raise ValueError("Invalid setup mail request.")
    with work_transaction():
        preview = verify_preview(request, service, preview_token)
        attempt_id = preview.draft.status.attempt_id
        previous = SetupMailDelivery.objects.filter(
            attempt_id=attempt_id, request_key=request_key
        ).first()
        if previous is not None:
            return _status(previous)
        if (
            not acknowledge_unknown
            and SetupMailDelivery.objects.filter(
                attempt_id=attempt_id, state="delivery_unknown"
            ).exists()
        ):
            raise ValueError(
                "A prior test may have arrived; acknowledge before sending again."
            )
        if SetupMailDelivery.objects.filter(
            attempt_id=attempt_id, state__in=["queued", "submitting"]
        ).exists():
            raise StaleRecordError("A setup mail test is already pending.")
        credential = SetupSealedCredential.objects.only(
            "id", "version", "fingerprint", "settings"
        ).get(attempt_id=attempt_id, target="google_workspace", scrubbed_at=None)
        sections = preview.compiled.candidate.document()["sections"]
        selected = next(
            (
                row["values"]
                for row in sections.get("content", [])
                if row["values"]["kind"] == "email" and row["values"]["slot"] == slot
            ),
            {
                "subject": "{{ parish_name }} readiness test",
                "html": "<p>{{ family_member_names }}, this is a readiness sample "
                "for {{ campaign_name }}.</p>",
                "text": "{{ family_member_names }}, this is a readiness sample "
                "for {{ campaign_name }}.",
            },
        )
        rendered = sample_render(
            selected,
            parish=sections["parish"][0]["values"],
            campaign=sections["campaigns"][0]["values"],
        )
        identifier = uuid5(attempt_id, "setup-mail:" + str(request_key))
        mail = ReadinessMail(
            delivery_id=identifier,
            **{
                key: credential.settings[key]
                for key in ("sender", "reply_to", "recipient")
            },
            **rendered,
        )
        row = SetupMailDelivery(
            id=identifier,
            attempt_id=attempt_id,
            attempt_version=preview.draft.status.version,
            request_key=request_key,
            candidate_digest=preview.compiled.candidate.digest,
            credential_id=credential.pk,
            credential_version=credential.version,
            fingerprint=credential.fingerprint,
            mail=mail.payload(),
            actor_id=request.portal_session.principal_id,
        )

        def admit(action, task):
            """Task creation repeats the exact current owner and target bindings."""
            return (
                action == "enqueue"
                and task.task_type == TASK_TYPE
                and task.domain_request_id == identifier
                and live(row)
            )

        task = enqueue(
            task_type=TASK_TYPE,
            domain_request_id=identifier,
            actor_id=row.actor_id,
            correlation_id=current_correlation(),
            admit=admit,
            idempotency_key=identifier,
        )
        row.task_id = task.run_id
        row.save(force_insert=True)
        authenticated_admin(request, store=service.store, activity=True)
        return _status(row)


def begin_submission(identifier, claim):
    """Commit the one-way send boundary before a caller can launch provider IO."""
    if connection.in_atomic_block:
        raise StorageInvariantError("Submission intent must commit before provider IO.")
    _identity("pk_stewardship_mail_dispatch")
    with work_transaction():
        task = lock_task_claim(claim)
        row = SetupMailDelivery.objects.select_for_update().get(pk=identifier)
        if (
            row.state != "queued"
            or task.root_id != row.task_id
            or task.domain_request_id != row.pk
            or not live(row)
        ):
            raise PermissionError("Setup mail submission is no longer admitted.")
        mail = ReadinessMail.from_payload(row.mail)
        SetupMailDelivery.objects.filter(pk=row.pk, version=row.version).update(
            state="submitting",
            run_id=claim.run_id,
            task_fence=claim.fence,
            worker_id=claim.worker_id,
            submitted_at=database_now(),
            deadline_at=database_now(),
            actor_id=claim.worker_id,
            correlation_id=current_correlation(),
            version=F("version") + 1,
        )
        row.refresh_from_db()
        return mail, row.deadline_at


def finish_submission(identifier, claim, outcome):
    """Record only a live original worker's closed result, never resubmit content."""
    if not isinstance(outcome, DeliveryOutcome):
        raise ValueError("A closed delivery outcome is required.")
    _identity("pk_stewardship_mail_dispatch")
    with work_transaction():
        lock_task_claim(claim)
        row = SetupMailDelivery.objects.select_for_update().get(pk=identifier)
        if (
            row.state != "submitting"
            or row.run_id != claim.run_id
            or row.task_fence != claim.fence
            or row.worker_id != claim.worker_id
        ):
            raise PermissionError("Only the original submission owner can finish.")

        def write(result):
            """Keep the original claim and row lock across any deadline retry."""
            SetupMailDelivery.objects.filter(pk=row.pk, version=row.version).update(
                state=result.value,
                finished_at=database_now(),
                actor_id=claim.worker_id,
                correlation_id=current_correlation(),
                version=F("version") + 1,
            )
            row.refresh_from_db()
            return _status(row)

        return settle_result(outcome, deadline=row.deadline_at, write=write)


def recover_pending(*, limit=100):
    """Cancel stale unsent work or retain expired in-flight uncertainty, never retry.

    A dead helper's possible provider effect cannot be inferred from a Task
    failure. Recovery waits for both the original claim and the helper's hard
    deadline to expire. It changes no Task history and sends no message.
    """
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("Invalid setup delivery recovery batch.")
    _identity("pk_stewardship_scheduler")
    with work_transaction():
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT delivery.id,delivery.state "
                "FROM public.stewardship_setup_mail_delivery delivery "
                "WHERE (delivery.state='queued' "
                "AND (NOT public.stewardship_setup_mail_live_v1("
                "delivery.attempt_id,delivery.attempt_version,delivery.credential_id,"
                "delivery.credential_version,delivery.fingerprint) "
                "OR EXISTS (SELECT 1 FROM public.stewardship_task_run original "
                "WHERE original.id=delivery.task_id "
                "AND original.state IN ('failed','cancelled')))) OR "
                "(delivery.state='submitting' "
                "AND delivery.deadline_at<=clock_timestamp() "
                "AND NOT EXISTS (SELECT 1 FROM public.stewardship_task_run task "
                "WHERE task.id=delivery.run_id AND task.state='running' "
                "AND task.fence=delivery.task_fence "
                "AND task.worker_id=delivery.worker_id "
                "AND task.lease_expires_at>clock_timestamp())) "
                "ORDER BY delivery.created_at,delivery.id "
                "LIMIT %s FOR UPDATE OF delivery",
                [limit],
            )
            found = cursor.fetchall()
        for identifier, state in found:
            SetupMailDelivery.objects.filter(pk=identifier).update(
                state="cancelled" if state == "queued" else "delivery_unknown",
                finished_at=database_now(),
                actor_id=None,
                correlation_id=current_correlation(),
                version=F("version") + 1,
            )
        return len(found)
