"""Final-submit receipt allocation: local atomic writes only, never provider I/O."""

from uuid import uuid4

from django.conf import settings

from parishkit.stewardship.campaigns.work_locks import require_work_order
from parishkit.stewardship.jobs.family_mail_inputs import load_family_mail_source
from parishkit.stewardship.jobs.outbox_validation import DeliveryIdentity
from parishkit.stewardship.jobs.receipt_rendering import current_receipt_render
from parishkit.stewardship.jobs.storage import enqueue

from .models import SubmissionReceiptOccurrence


def create_submission_receipt(submission, *, runtime, campaign, family):
    """Commit one concrete outbox item or a current-source no-recipient outcome.

    This composes inside final Submit's transaction. The narrow receipt command
    creates the outbox/render/history without giving Web generic delivery write
    privileges. Its SQL guard consumes preparation and verifies all bindings.
    """
    require_work_order()
    source = load_family_mail_source(family)
    message_id, preparation = None, None
    if source.recipients.deliverable:
        message_id = uuid4()
        identity = DeliveryIdentity(
            scope_id=campaign.pk,
            campaign_id=campaign.pk,
            family_id=family.pk,
            semantic_key=submission.pk,
            mode="production" if submission.mode == "live" else "testing",
            routing="production" if submission.mode == "live" else "testing_override",
            purpose="receipt",
        )
        render = current_receipt_render(
            identity,
            submission,
            runtime=runtime,
            campaign=campaign,
            source=source,
            public_origin=settings.STEWARDSHIP_PUBLIC_ORIGIN,
        )
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
        content = render.fields()
        # JSON command input has UUID strings; the immutable renderer stays typed.
        content["configuration_id"] = str(content["configuration_id"])
        if content["template_id"] is not None:
            content["template_id"] = str(content["template_id"])
        preparation = {"task_id": str(task.run_id), "render": content}
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
