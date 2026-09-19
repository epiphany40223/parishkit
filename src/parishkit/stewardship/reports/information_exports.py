"""Closed additional-information allocation through the shared export lifecycle."""

from uuid import UUID, uuid4

from parishkit.stewardship.accounts.policy import Capability
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.audit.schemas import Action, Outcome
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.jobs.storage import enqueue
from parishkit.stewardship.schema_primitives import timezone_names

from .export_models import ExportRequest, InformationExportSnapshot
from .export_services import TASK_TYPE, admit_campaign, audit, authorize
from .information import InformationQuery

REPORT = "additional_information"


def create_information_export(
    store,
    user_id,
    *,
    campaign_id,
    query,
    history,
    format,
    browser_timezone,
    request_key,
    snapshot=None,
):
    """Capture all matches once; trusted regeneration supplies the retained snapshot.

    The campaign-work transaction serializes allocation/capture with mutation and
    purge producers. Database capture is one coherent statement, not a paginated
    series which could mix concurrent edits. File rendering stays asynchronous.
    Snapshot is an internal service argument, never accepted by the web form.
    """
    if any(
        not isinstance(value, UUID) for value in (user_id, campaign_id, request_key)
    ):
        raise ValueError("Export identities must be canonical UUIDs.")
    if not isinstance(query, InformationQuery) or type(history) is not bool:
        raise ValueError(
            "Information export requires typed filters and history choice."
        )
    parameters = {
        "filters": InformationQuery.parse(query.form_values()).form_values(),
        "history": history,
    }
    if type(format) is not str or format not in {"csv", "xlsx", "pdf"}:
        raise ValueError("Unsupported information export format.")
    if type(browser_timezone) is not str or browser_timezone not in timezone_names():
        raise ValueError("Export timezone must be an IANA name.")
    if snapshot is not None and not isinstance(snapshot, InformationExportSnapshot):
        raise TypeError("Regeneration requires a retained information snapshot.")

    def admit(*args):
        """Both enqueue and its owning transaction recheck current authorization."""
        authorize(store, user_id)
        admit_campaign(campaign_id, mutating=True)
        return True

    with work_transaction():
        admit()
        previous = ExportRequest.objects.filter(
            requester_id=user_id, request_key=request_key
        ).first()
        if previous is not None:
            if (
                previous.report,
                previous.campaign_id,
                previous.parameters,
                previous.format,
                previous.browser_timezone,
            ) != (REPORT, campaign_id, parameters, format, browser_timezone) or (
                snapshot is not None and previous.information_snapshot_id != snapshot.pk
            ):
                raise ValueError("Export request identity is already bound.")
            return previous
        configuration_id = SystemConfiguration.objects.get().active_configuration_id
        correlation_id = uuid4()
        if snapshot is None:
            snapshot = InformationExportSnapshot.objects.create(
                campaign_id=campaign_id,
                configuration_id=configuration_id,
                actor_id=user_id,
                correlation_id=correlation_id,
                parameters=parameters,
            )
            # The insert trigger, never client-supplied values, owns captured data.
            snapshot.refresh_from_db()
        elif snapshot.campaign_id != campaign_id or snapshot.parameters != parameters:
            raise ValueError("Retained information scope differs from the request.")
        identifier = uuid4()
        task = enqueue(
            task_type=TASK_TYPE,
            domain_request_id=identifier,
            actor_id=user_id,
            correlation_id=correlation_id,
            idempotency_key=identifier,
            admit=admit,
        )
        request = ExportRequest.objects.create(
            id=identifier,
            campaign_id=campaign_id,
            requester_id=user_id,
            request_key=request_key,
            task_id=task.run_id,
            information_snapshot=snapshot,
            configuration_id=configuration_id,
            report=REPORT,
            format=format,
            browser_timezone=browser_timezone,
            parameters=parameters,
            authorization_scope={"capability": Capability.CAMPAIGN_REPORT.value},
            actor_id=user_id,
            correlation_id=correlation_id,
        )
        audit(
            Action.EXPORT_REQUESTED,
            request,
            user_id,
            outcome=Outcome.STARTED,
            count=snapshot.row_count,
        )
        return request
