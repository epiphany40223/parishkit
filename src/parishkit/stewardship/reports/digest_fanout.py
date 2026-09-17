"""Bounded, individually addressed mail intents over retained daily content."""

from uuid import UUID, uuid4

from parishkit.email.base import InlineImage
from parishkit.stewardship.accounts.configuration_models import AppliedIntegration
from parishkit.stewardship.accounts.content_models import ContentVersion
from parishkit.stewardship.campaigns.catchup_ownership import claim_event
from parishkit.stewardship.campaigns.models import ScheduleRevision
from parishkit.stewardship.campaigns.work_locks import require_work_order
from parishkit.stewardship.jobs.campaign_mail_values import campaign_values
from parishkit.stewardship.jobs.digest_content import (
    DigestTemplate,
    render_digest_envelope,
)
from parishkit.stewardship.jobs.outbox_storage import create_message
from parishkit.stewardship.jobs.outbox_validation import DeliveryIdentity
from parishkit.stewardship.jobs.ownership import lock_task_claim
from parishkit.stewardship.jobs.storage import _status
from parishkit.stewardship.web.digest_content import CHART_ID, validate_digest_body

from .daily_digest import DailyDigestContent
from .digest_models import DailyDigestReady, DailyDigestRecipient
from .digest_ownership import (
    bound_preparation,
    checkpoint_preparation,
    current_preparation,
)

LIMIT = 25


def retained_render(identity, ready, scope, revision, address):
    """Prepend current safe prose without recapturing any report facts or chart."""
    require_work_order()
    version = scope.runtime.active_configuration
    template = ContentVersion.objects.get(
        configuration=version,
        campaign_id=identity.campaign_id,
        kind="email",
        record_id=UUID(revision.values["template_version"]),
    )
    email = AppliedIntegration.objects.get(configuration=version, kind="email")
    content = DailyDigestContent(
        ready.subject, ready.html, ready.text, InlineImage(bytes(ready.chart), CHART_ID)
    )
    render = render_digest_envelope(
        identity=identity,
        configuration_id=version.pk,
        template_id=template.pk,
        template=DigestTemplate(template.subject, template.html, template.text),
        content=content,
        values=campaign_values(
            parish=version.canonical_document["sections"]["parish"][0]["values"],
            campaign=scope.campaign.active_configuration.values,
        ),
        sender=email.settings["sender"],
        reply_to=email.settings["reply_to"],
        recipient=address,
        testing_recipient=scope.runtime.testing_recipient
        if identity.mode == "testing"
        else None,
    )
    validate_digest_body(render.html, render.text, content.chart.data)
    return render


def fanout_daily(claim):
    """Allocate at most one page of one-Admin messages under exact task ownership.

    Each recipient binding and its outbox/task/render commit together. Replayed
    pages skip retained bindings instead of reconstructing an earlier message
    with a new template, sender or worker identity. MAIL cannot send any child
    until the parent preparation has completed the entire cohort.
    """
    require_work_order()
    row = bound_preparation(_status(lock_task_claim(claim)))
    scope = current_preparation(row)
    if row.phase != "fanout":
        raise PermissionError("Daily recipient allocation is not ready.")
    ready = DailyDigestReady.objects.get(snapshot__preparation=row)
    revision = ScheduleRevision.objects.get(pk=row.revision_id)
    existing = set(
        DailyDigestRecipient.objects.filter(ready=ready).values_list(
            "address", flat=True
        )
    )
    pending = [address for address in ready.recipients if address not in existing]
    correlation_id = claim_event(claim)
    for address in pending[:LIMIT]:
        identifier = uuid4()
        identity = DeliveryIdentity(
            scope_id=row.campaign_id,
            campaign_id=row.campaign_id,
            semantic_key=identifier,
            mode=row.mode,
            routing="testing_override" if row.mode == "testing" else "production",
            purpose="daily_digest",
        )
        render = retained_render(identity, ready, scope, revision, address)

        def admit(action, candidate, status, *, expected=identity):
            """Only this current page may allocate this exact semantic delivery."""
            lock_task_claim(claim)
            current_preparation(row)
            return action in {"create", "create_task"} and candidate == expected

        message = create_message(
            identity=identity,
            render=render,
            actor_id=claim.worker_id,
            correlation_id=correlation_id,
            command_id=identifier,
            admit=admit,
        )
        DailyDigestRecipient.objects.create(
            id=identifier,
            ready=ready,
            address=address,
            outbox_id=message.message_id,
            actor_id=claim.worker_id,
            correlation_id=correlation_id,
        )
    return checkpoint_preparation(
        claim, phase="complete" if len(pending) <= LIMIT else "fanout"
    )
