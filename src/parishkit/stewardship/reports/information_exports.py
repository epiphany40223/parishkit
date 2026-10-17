"""Closed additional-information allocation through the shared export lifecycle."""

from uuid import UUID, uuid4

from parishkit.stewardship.accounts.policy import Capability
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.audit.schemas import Action, Outcome
from parishkit.stewardship.campaigns.work_locks import export_transaction
from parishkit.stewardship.jobs.storage import enqueue
from parishkit.stewardship.schema_primitives import timezone_names

from .export_models import ExportRequest, InformationExportSnapshot
from .export_services import (
    TASK_TYPE,
    ExportRequestBound,
    admit_campaign,
    audit,
    authorize,
)
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
):
    """Capture all matches once, as the page's export form does.

    The campaign-work transaction serializes allocation/capture with mutation and
    purge producers. Database capture is one coherent statement, not a paginated
    series which could mix concurrent edits. File rendering stays asynchronous.
    """
    if any(
        not isinstance(value, UUID) for value in (user_id, campaign_id, request_key)
    ):
        raise ValueError("Export identities must be canonical UUIDs.")
    if not isinstance(query, InformationQuery) or type(history) is not bool:
        raise ValueError(
            "Information export requires typed filters and history choice."
        )
    # Re-parsed, so only the closed grammar's own values are captured;
    # retained() also keeps filters that carry no zone (as an older release
    # wrote them), which SQL reads as campaign days.
    parameters = {
        "filters": InformationQuery.retained(query.form_values()).form_values(),
        "history": history,
    }
    if type(format) is not str or format not in {"csv", "xlsx", "pdf"}:
        raise ValueError("Unsupported information export format.")
    if type(browser_timezone) is not str or browser_timezone not in timezone_names():
        raise ValueError("Export timezone must be an IANA name.")

    def admit(*args):
        """Both enqueue and its owning transaction recheck current authorization."""
        authorize(store, user_id)
        admit_campaign(campaign_id, mutating=True)
        return True

    with export_transaction(campaign_id):
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
            ) != (REPORT, campaign_id, parameters, format, browser_timezone):
                raise ExportRequestBound("Export request identity is already bound.")
            return previous
        configuration_id = SystemConfiguration.objects.get().active_configuration_id
        correlation_id = uuid4()
        snapshot = InformationExportSnapshot.objects.create(
            campaign_id=campaign_id,
            configuration_id=configuration_id,
            actor_id=user_id,
            correlation_id=correlation_id,
            parameters=parameters,
        )
        # The insert trigger, never client-supplied values, owns captured data.
        snapshot.refresh_from_db()
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
