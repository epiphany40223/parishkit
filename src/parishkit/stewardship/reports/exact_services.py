"""Requester services for queued, immutable current-input participation exports."""

from uuid import UUID, uuid4

from django.db import transaction

from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.audit.schemas import Action, Outcome
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.jobs.storage import TaskRetryConflict, enqueue, retry_failed
from parishkit.stewardship.schema_primitives import timezone_names
from parishkit.stewardship.source.pins import pin_snapshot

from .exact_models import (
    ExactExportCancellation,
    ExactExportRequest,
    ExactExportResolution,
)
from .export_services import (
    admit_campaign,
    audit,
    authorize,
    cancel_export,
    export_status,
    retry_export,
)
from .facts import FactUnavailable
from .models import POPULATION_SCOPES
from .selection import current_inputs

TASK_TYPE = "report_exact_export"


def create_exact_export(
    store,
    user_id,
    *,
    campaign_id,
    population_scope,
    format,
    browser_timezone,
    request_key,
):
    """Freeze once and protect current-scope source rows before queuing work.

    Replay compares user intent before recapturing inputs. An honest retry after
    a source promotion or midnight therefore returns the original request.
    """
    if any(
        not isinstance(value, UUID) for value in (user_id, campaign_id, request_key)
    ):
        raise ValueError("Export identities must be canonical UUIDs.")
    if type(population_scope) is not str or population_scope not in POPULATION_SCOPES:
        raise ValueError("Unknown report population scope.")
    if type(format) is not str or format not in {"csv", "png", "pdf"}:
        raise ValueError("Unsupported participation export format.")
    if type(browser_timezone) is not str or browser_timezone not in timezone_names():
        raise ValueError("Export timezone must be an IANA name.")

    def admit(*args):
        """Recheck coherent requester policy and lifecycle at allocation effects."""
        authorize(store, user_id)
        admit_campaign(campaign_id, mutating=True)
        return True

    with work_transaction():
        admit()
        previous = ExactExportRequest.objects.filter(
            requester_id=user_id, request_key=request_key
        ).first()
        if previous is not None:
            if (
                previous.campaign_id,
                previous.population_scope,
                previous.format,
                previous.browser_timezone,
            ) != (campaign_id, population_scope, format, browser_timezone):
                raise ValueError("Export request identity is already bound.")
            return previous
        inputs, _ = current_inputs(campaign_id, population_scope)
        if inputs is None:
            raise FactUnavailable("Campaign report inputs are unavailable.")
        identifier, correlation_id = uuid4(), uuid4()
        task = enqueue(
            task_type=TASK_TYPE,
            domain_request_id=identifier,
            actor_id=user_id,
            correlation_id=correlation_id,
            idempotency_key=identifier,
            admit=admit,
        )
        request = ExactExportRequest.objects.create(
            id=identifier,
            campaign_id=campaign_id,
            requester_id=user_id,
            request_key=request_key,
            task_id=task.run_id,
            configuration_id=SystemConfiguration.objects.get().active_configuration_id,
            population_scope=population_scope,
            source_id=inputs.source_id,
            submission_watermark=inputs.submission_watermark,
            timezone_configuration_id=inputs.timezone_configuration_id,
            through_date=inputs.through_date,
            format=format,
            browser_timezone=browser_timezone,
            actor_id=user_id,
            correlation_id=correlation_id,
        )
        if population_scope == "current":
            pin_snapshot(
                inputs.source_id,
                parent_kind="report",
                parent_id=request.pk,
                admit=admit,
            )
        audit(Action.EXPORT_REQUESTED, request, user_id, outcome=Outcome.STARTED)
        return request


def cancel_exact_export(store, user_id, request_id):
    """Serialize cancellation with handoff and delegate once the export exists."""
    with work_transaction():
        request = ExactExportRequest.objects.get(pk=request_id)
        authorize(store, user_id, request=request)
        admit_campaign(request.campaign_id, mutating=True)
        resolution = ExactExportResolution.objects.filter(request=request).first()
        if resolution is not None:
            return cancel_export(store, user_id, resolution.export_id)
        receipt, created = ExactExportCancellation.objects.get_or_create(
            request=request,
            defaults={"actor_id": user_id},
        )
        if created:
            audit(Action.EXPORT_CANCELLED, request, user_id, outcome=Outcome.CANCELLED)
        return receipt


def retry_exact_export(store, user_id, request_id, *, request_key):
    """Keep original inputs/budgets; resolved failures belong downstream."""
    from .exact_tasks import admit_exact

    with work_transaction():
        request = ExactExportRequest.objects.get(pk=request_id)
        authorize(store, user_id, request=request)
        admit_campaign(request.campaign_id, mutating=True)
        resolution = ExactExportResolution.objects.filter(request=request).first()
        if resolution is not None:
            return retry_export(
                store, user_id, resolution.export_id, request_key=request_key
            )
        if ExactExportCancellation.objects.filter(request=request).exists():
            raise TaskRetryConflict("Cancelled exact exports cannot be retried.")
        previous = request.task.chain_runs.filter(retry_command_id=request_key).first()
        latest = request.task.chain_runs.order_by("-retry_sequence").first()
        return retry_failed(
            run_id=previous.parent_id if previous is not None else latest.pk,
            command_id=request_key,
            actor_id=user_id,
            correlation_id=uuid4(),
            admit=lambda action, status: admit_exact(action, status, store=store),
        )


def exact_export_status(store, user_id, request_id):
    """Expose real task status with the original request identity."""
    with transaction.atomic():
        request = ExactExportRequest.objects.get(pk=request_id)
        authorize(store, user_id, request=request)
        admit_campaign(request.campaign_id, mutating=False)
        resolution = ExactExportResolution.objects.filter(request=request).first()
        result = {
            "id": str(request.pk),
            "campaign_id": str(request.campaign_id),
            "report": "participation",
            "format": request.format,
            "population_scope": request.population_scope,
            "created_at": request.created_at.isoformat(),
            "export_id": None,
            "expires_at": None,
        }
        if resolution is not None:
            downstream = export_status(store, user_id, resolution.export_id)
            result.update(
                state=downstream["state"],
                export_id=str(resolution.export_id),
                expires_at=downstream["expires_at"],
            )
        elif ExactExportCancellation.objects.filter(request=request).exists():
            result["state"] = "cancelled"
        else:
            result["state"] = (
                request.task.chain_runs.order_by("-retry_sequence").first().state
            )
        return result
