"""Owning authorization and immutable request allocation for compiled reports."""

from datetime import timedelta
from uuid import UUID, uuid4

from django.db import connection, transaction

from parishkit.stewardship.accounts.policy import Capability, allows, current_principal
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.jobs.ownership import database_now
from parishkit.stewardship.jobs.storage import enqueue, retry_failed
from parishkit.stewardship.schema_primitives import timezone_names

from .export_models import (
    ExportCancellation,
    ExportDownloadGrant,
    ExportDownloadUse,
    ExportPublication,
    ExportRequest,
)
from .facts import FactUnavailable
from .models import CampaignDailyFactSet
from .retention import pin_facts

TASK_TYPE = "report_export"


class ExportConflict(ValueError):
    """A valid command conflicts with an already completed export."""


def authorize(store, user_id, *, request=None):
    """Reload current coherent policy; possession of an opaque UUID is not access."""
    principal = current_principal(store, user_id)
    if not allows(principal, Capability.CAMPAIGN_REPORT) or (
        request is not None
        and principal.identity != request.requester_id
        and "administrator" not in principal.roles
    ):
        raise PermissionError("This export is unavailable.")
    return principal


def admit_campaign(campaign_id, *, mutating):
    """Retained campaigns need no current-pointer/live-date or mail-pause exemption."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT stewardship_export_admitted_v1(%s,%s)", (campaign_id, mutating)
        )
        if cursor.fetchone() != (True,):
            raise PermissionError("Campaign reporting is currently unavailable.")


def audit(action, request, actor_id, *, outcome, count=None):
    """Reference the retained request; do not duplicate report rows or filter values."""
    context = {"outcome": outcome}
    if count is not None:
        context["count"] = count
    record_action(
        action,
        actor_kind=ActorKind.PORTAL_USER,
        actor_id=actor_id,
        subject_id=request.pk,
        parish_id=request.configuration.parish.pk,
        campaign_id=request.campaign_id,
        context=context,
    )


def create_export(
    store, user_id, *, campaign_id, fact_set_id, format, browser_timezone, request_key
):
    """Atomically pin a ready exact generation and allocate one requester task.

    The initial compiled report has no identifying filters, selected rows or
    arbitrary sort expressions. Those are recorded explicitly rather than
    accepting a generic caller-controlled query language before its owners exist.
    """
    if any(
        not isinstance(value, UUID)
        for value in (user_id, campaign_id, fact_set_id, request_key)
    ):
        raise ValueError("Export identities must be canonical UUIDs.")
    if type(format) is not str or format not in {"csv", "png", "pdf", "xlsx"}:
        raise ValueError("Unsupported participation export format.")
    if type(browser_timezone) is not str or browser_timezone not in timezone_names():
        raise ValueError("Export timezone must be an IANA name.")
    with work_transaction():
        authorize(store, user_id)
        admit_campaign(campaign_id, mutating=True)
        previous = ExportRequest.objects.filter(
            requester_id=user_id, request_key=request_key
        ).first()
        if previous is not None:
            if (
                previous.campaign_id,
                previous.fact_set_id,
                previous.format,
                previous.browser_timezone,
            ) != (campaign_id, fact_set_id, format, browser_timezone):
                raise ValueError("Export request identity is already bound.")
            return previous
        facts = (
            CampaignDailyFactSet.objects.select_for_update()
            .filter(pk=fact_set_id, campaign_id=campaign_id, state="ready")
            .first()
        )
        if facts is None:
            raise FactUnavailable("The exact requested calculation is not ready.")
        identifier, correlation_id = uuid4(), uuid4()
        task = enqueue(
            task_type=TASK_TYPE,
            domain_request_id=identifier,
            actor_id=user_id,
            correlation_id=correlation_id,
            idempotency_key=identifier,
            admit=lambda action, status: _creation_admission(
                store, user_id, campaign_id
            ),
        )
        request = ExportRequest.objects.create(
            id=identifier,
            campaign_id=campaign_id,
            requester_id=user_id,
            request_key=request_key,
            task_id=task.run_id,
            fact_set=facts,
            configuration_id=SystemConfiguration.objects.get().active_configuration_id,
            report="participation",
            format=format,
            browser_timezone=browser_timezone,
            parameters={
                "population_scope": facts.population_scope,
                "sort": "date_asc",
                "filters": {},
                "selected_ids": [],
            },
            authorization_scope={"capability": Capability.CAMPAIGN_REPORT.value},
            actor_id=user_id,
            correlation_id=correlation_id,
        )
        pin_facts(
            facts.pk,
            parent_kind="export",
            parent_id=request.pk,
            admit=lambda action, inputs: _creation_admission(
                store, user_id, campaign_id
            ),
        )
        audit(Action.EXPORT_REQUESTED, request, user_id, outcome=Outcome.STARTED)
        return request


def _creation_admission(store, user_id, campaign_id):
    """Repeat admission under the allocation owner's existing work transaction."""
    authorize(store, user_id)
    admit_campaign(campaign_id, mutating=True)
    return True


def cancel_export(store, user_id, request_id):
    """Request safe-point cancellation without publishing partial files."""
    with work_transaction():
        request = ExportRequest.objects.get(pk=request_id)
        authorize(store, user_id, request=request)
        admit_campaign(request.campaign_id, mutating=True)
        if ExportPublication.objects.filter(request=request).exists():
            raise ExportConflict("Completed exports cannot be cancelled.")
        receipt, created = ExportCancellation.objects.get_or_create(
            request=request,
            defaults={"actor_id": user_id},
        )
        if created:
            audit(Action.EXPORT_CANCELLED, request, user_id, outcome=Outcome.CANCELLED)
        return receipt


def regenerate_export(store, user_id, request_id, *, request_key):
    """Create a new request for expired retained facts, never recapture current data.

    Regeneration has a new request time and current parish presentation policy.
    The retained fact set (including its source, cutoff and campaign timezone
    configuration), format and browser timezone stay fixed. Presentation uses
    the current configuration; the old publication and audit remain immutable.
    """
    with work_transaction():
        original = ExportRequest.objects.get(pk=request_id)
        authorize(store, user_id, request=original)
        admit_campaign(original.campaign_id, mutating=True)
        publication = ExportPublication.objects.filter(request=original).first()
        if publication is None or publication.expires_at > database_now():
            raise ExportConflict("Only expired exports can be regenerated.")
        return create_export(
            store,
            user_id,
            campaign_id=original.campaign_id,
            fact_set_id=original.fact_set_id,
            format=original.format,
            browser_timezone=original.browser_timezone,
            request_key=request_key,
        )


def retry_export(store, user_id, request_id, *, request_key):
    """Retry the latest failed run without changing the original report owner.

    The command actor owns this retry journal entry; the immutable export and
    root task retain the original requester whose policy the worker rechecks.
    This service is exercised here; its requester-facing controls belong to the
    Phase 5 report-job UI. Operational cleanup already has its own Admin form.
    """
    from .export_tasks import admit_export

    with work_transaction():
        request = ExportRequest.objects.get(pk=request_id)
        authorize(store, user_id, request=request)
        admit_campaign(request.campaign_id, mutating=True)
        previous = request.task.chain_runs.filter(retry_command_id=request_key).first()
        latest = request.task.chain_runs.order_by("-retry_sequence").first()
        return retry_failed(
            run_id=previous.parent_id if previous is not None else latest.pk,
            command_id=request_key,
            actor_id=user_id,
            correlation_id=uuid4(),
            admit=lambda action, status: admit_export(action, status, store=store),
        )


def export_status(store, user_id, request_id):
    """Return only this authorized request, never another operational task's fields."""
    with transaction.atomic():
        request = ExportRequest.objects.get(pk=request_id)
        authorize(store, user_id, request=request)
        admit_campaign(request.campaign_id, mutating=False)
        publication = ExportPublication.objects.filter(request=request).first()
        if publication is not None:
            state = "ready" if publication.expires_at > database_now() else "expired"
        elif ExportCancellation.objects.filter(request=request).exists():
            state = "cancelled"
        else:
            state = request.task.chain_runs.order_by("-retry_sequence").first().state
        return {
            "id": str(request.pk),
            "campaign_id": str(request.campaign_id),
            "report": request.report,
            "format": request.format,
            "state": state,
            "created_at": request.created_at.isoformat(),
            "expires_at": publication.expires_at.isoformat() if publication else None,
        }


def issue_download(store, user_id, request_id):
    """Issue parish-owned security metadata, including during purge preparation."""
    with transaction.atomic():
        request = ExportRequest.objects.get(pk=request_id)
        authorize(store, user_id, request=request)
        admit_campaign(request.campaign_id, mutating=False)
        publication = ExportPublication.objects.get(request=request)
        if publication.expires_at <= database_now():
            raise PermissionError("This export has expired.")
        return ExportDownloadGrant.objects.create(
            publication_id=publication.pk,
            requester_id=user_id,
            actor_id=user_id,
            expires_at=database_now() + timedelta(seconds=60),
        )


def consume_download(store, user_id, grant_id):
    """Consume once; the response must still perform its fresh guarded checks."""
    with transaction.atomic():
        grant = ExportDownloadGrant.objects.get(pk=grant_id, requester_id=user_id)
        publication = ExportPublication.objects.select_related("request").get(
            pk=grant.publication_id
        )
        request = publication.request
        authorize(store, user_id, request=request)
        admit_campaign(request.campaign_id, mutating=False)
        if min(grant.expires_at, publication.expires_at) <= database_now():
            raise PermissionError("This download grant is unavailable.")
        # A unique constraint wins concurrent uses without exposing grant material
        # in audit. A failed/aborted response consumes its grant, not the artifact.
        ExportDownloadUse.objects.create(grant=grant, actor_id=user_id)
        audit(
            Action.EXPORT_DOWNLOADED,
            request,
            user_id,
            outcome=Outcome.STARTED,
            count=publication.row_count,
        )
        return publication
