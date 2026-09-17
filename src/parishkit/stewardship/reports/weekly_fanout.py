"""Compile outside lifecycle locks; atomically retain one bounded Admin page."""

import json
from dataclasses import dataclass
from uuid import UUID, uuid4

from django.db import connection

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
from parishkit.stewardship.storage import StorageInvariantError

from .weekly_capture import retained_selection
from .weekly_digest import WeeklyDigestDocument, render_weekly_digest
from .weekly_models import (
    WeeklyDigestRecipient,
    WeeklyDigestSnapshot,
    WeeklyManualRequest,
)
from .weekly_ownership import (
    bound_preparation,
    checkpoint_preparation,
    current_preparation,
)
from .weekly_selection import WeeklySelection

LIMIT = 25


class WeeklyCoverageChanged(StorageInvariantError):
    """A detached page must be reloaded after a newly committed coverage proof."""


@dataclass(frozen=True, repr=False)
class WeeklyRecipientPlan:
    """Private detached subset and the actual messages covering omitted items."""

    address: str
    information: tuple[str, ...]
    corrections: tuple[tuple[str, str], ...]
    covered_messages: tuple[str, ...]
    document: WeeklyDigestDocument | None


@dataclass(frozen=True, repr=False)
class WeeklyPage:
    """A reproducible bounded page, rechecked under the committing task claim."""

    snapshot_id: UUID
    recipients: tuple[WeeklyRecipientPlan, ...]
    last: bool


def load_weekly_page(claim):
    """Detach only uncovered items; queued or uncertain messages never cover them."""
    require_work_order()
    row = bound_preparation(_status(lock_task_claim(claim)))
    current_preparation(row)
    if row.phase != "fanout":
        raise PermissionError("Weekly recipient allocation is not ready.")
    snapshot = WeeklyDigestSnapshot.objects.select_related(
        "configuration__parish", "timezone_configuration"
    ).get(preparation=row)
    selected = retained_selection(snapshot)
    manual = WeeklyManualRequest.objects.filter(pk=row.pk).exists()
    existing = set(
        WeeklyDigestRecipient.objects.filter(snapshot=snapshot).values_list(
            "address", flat=True
        )
    )
    pending = (
        []
        if selected.empty
        else [address for address in snapshot.recipients if address not in existing]
    )
    plans = []
    for address in pending[:LIMIT]:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT stewardship_weekly_prior_items_v1(%s,%s)::text",
                [snapshot.pk, address],
            )
            coverage = json.loads(cursor.fetchone()[0])
        information = tuple(coverage["information"])
        corrections = tuple(tuple(pair) for pair in coverage["corrections"])
        wanted = set(information)
        corrected = set(corrections)
        subset = WeeklySelection(
            selected.observation,
            tuple(item for item in selected.information if str(item.item_id) in wanted),
            tuple(
                item
                for item in selected.corrections
                if (str(item.item_id), item.disposition) in corrected
            ),
        )
        document = (
            None
            if subset.empty
            else subset.document(
                snapshot_id=snapshot.pk,
                parish_name=snapshot.configuration.parish.name,
                campaign_name=snapshot.timezone_configuration.name,
                campaign_timezone=snapshot.timezone_configuration.timezone,
                manual=manual,
            )
        )
        plans.append(
            WeeklyRecipientPlan(
                address,
                information,
                corrections,
                tuple(coverage["covered_messages"]),
                document,
            )
        )
    return WeeklyPage(snapshot.pk, tuple(plans), len(pending) <= LIMIT)


def compile_weekly_page(page, *, public_origin):
    """Render detached bytes without holding a database or lifecycle transaction."""
    if connection.in_atomic_block:
        raise StorageInvariantError("Weekly compilation must run outside transactions.")
    return tuple(
        None
        if item.document is None
        else render_weekly_digest(item.document, public_origin=public_origin)
        for item in page.recipients
    )


def retained_render(identity, content, scope, revision, address):
    """Apply current safe email prose/routing to already compiled private content."""
    require_work_order()
    version = scope.runtime.active_configuration
    template = ContentVersion.objects.get(
        configuration=version,
        campaign_id=identity.campaign_id,
        kind="email",
        record_id=UUID(revision.values["template_version"]),
    )
    email = AppliedIntegration.objects.get(configuration=version, kind="email")
    return render_digest_envelope(
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


def retain_weekly_page(claim, page, contents):
    """Recheck coverage and bind every private recipient to its exact outbox atomically.

    Provider reconciliation may have completed while compilation ran. A changed
    page must be reloaded instead of publishing stale coverage or replaying an
    accepted item. Every successful page remains immutable across later retries.
    """
    require_work_order()
    if load_weekly_page(claim) != page or len(contents) != len(page.recipients):
        raise WeeklyCoverageChanged(
            "Weekly recipient coverage changed during compilation."
        )
    row = bound_preparation(_status(lock_task_claim(claim)))
    scope = current_preparation(row)
    revision = ScheduleRevision.objects.get(pk=row.revision_id)
    correlation_id = claim_event(claim)
    for item, content in zip(page.recipients, contents, strict=True):
        identifier = uuid4()
        message_id = None
        if item.document is None:
            if content is not None or not item.covered_messages:
                raise StorageInvariantError(
                    "Weekly recipient requires accepted coverage."
                )
        else:
            identity = DeliveryIdentity(
                scope_id=row.campaign_id,
                campaign_id=row.campaign_id,
                semantic_key=identifier,
                mode=row.mode,
                routing="testing_override" if row.mode == "testing" else "production",
                purpose="weekly_digest",
            )
            render = retained_render(identity, content, scope, revision, item.address)

            def admit(action, candidate, status, *, expected=identity):
                """Admit only this exact allocation under the current owned page."""
                lock_task_claim(claim)
                current_preparation(row)
                return action in {"create", "create_task"} and candidate == expected

            message_id = create_message(
                identity=identity,
                render=render,
                actor_id=claim.worker_id,
                correlation_id=correlation_id,
                command_id=identifier,
                admit=admit,
            ).message_id
        WeeklyDigestRecipient.objects.create(
            id=identifier,
            snapshot_id=page.snapshot_id,
            address=item.address,
            information=list(item.information),
            corrections=[list(pair) for pair in item.corrections],
            covered_messages=list(item.covered_messages),
            subject=content.subject if content is not None else "",
            html=content.html if content is not None else "",
            text=content.text if content is not None else "",
            outbox_id=message_id,
            actor_id=claim.worker_id,
            correlation_id=correlation_id,
        )
    return checkpoint_preparation(claim, phase="complete" if page.last else "fanout")
