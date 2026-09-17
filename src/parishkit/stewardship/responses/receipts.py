"""Final-submit receipt allocation: local atomic writes only, never provider I/O."""

from uuid import uuid4

from parishkit.stewardship.campaigns.work_locks import require_work_order
from parishkit.stewardship.jobs.family_mail_inputs import load_family_mail_source
from parishkit.stewardship.jobs.storage import enqueue

from .models import SubmissionReceiptOccurrence


def create_submission_receipt(submission, *, family):
    """Commit one concrete outbox item or a current-source no-recipient outcome.

    This composes inside final Submit's transaction. The narrow receipt command
    creates a non-sendable seed render without accepting any Web-supplied body.
    MAIL renders the final content from public facts and cannot read answers.
    The SQL guard consumes preparation and independently verifies all bindings.
    """
    require_work_order()
    source = load_family_mail_source(family)
    message_id, preparation = None, None
    if source.recipients.deliverable:
        message_id = uuid4()
        task = enqueue(
            task_type="outbox_delivery",
            domain_request_id=message_id,
            actor_id=family.pk,
            correlation_id=submission.correlation_id,
            idempotency_key=message_id,
            admit=lambda action, candidate: (
                action == "enqueue"
                and candidate.domain_request_id == message_id
                and candidate.state == "queued"
            ),
        )
        preparation = {"task_id": str(task.run_id)}
    row = SubmissionReceiptOccurrence.objects.create(
        submission=submission,
        disposition="queued" if message_id else "no_deliverable_recipient",
        outbox_id=message_id,
        preparation=preparation,
        actor_id=family.pk,
        correlation_id=submission.correlation_id,
    )
    # Do not leave the consumed command available through the returned ORM row.
    row.preparation = None
    return row
