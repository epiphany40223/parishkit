"""Complete financial stewardship captures through the shared export lifecycle.

The interactive page and the export read one SQL projection. The capture is
taken by the snapshot table's insert trigger, complete and unpaged, with the
application's giving proof inside the retained parameters, so a retained file
means exactly what the page meant when it was queued: proven source totals or
unavailable ones, never a later promotion's money.
"""

from uuid import UUID, uuid4

from parishkit.stewardship.accounts.policy import Capability, allows
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.audit.schemas import Action, Outcome
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.jobs.storage import enqueue
from parishkit.stewardship.schema_primitives import timezone_names

from .export_models import ExportRequest, FinancialExportSnapshot
from .export_services import TASK_TYPE, admit_campaign, audit, authorize
from .financial import FinancialQuery, giving_proof, shape_result
from .financial_documents import financial_document

REPORT = "financial"


def _campaign(campaign_id, *, capturing):
    """The campaign with its own configuration.

    A fresh capture needs the financial module on, as the page does; SQL would
    refuse the capture anyway, but as an outage rather than a denial.
    Regenerating a retained capture renders what was captured and needs no
    module, so a later change of modules cannot strand an expired file.
    """
    campaign = Campaign.objects.select_related("active_configuration").get(
        pk=campaign_id
    )
    if capturing and "financial" not in campaign.active_configuration.values.get(
        "modules", ()
    ):
        raise PermissionError("Financial stewardship is not enabled for this campaign.")
    return campaign


def create_financial_export(
    store,
    user_id,
    *,
    campaign_id,
    format,
    browser_timezone,
    request_key,
    query=None,
    snapshot=None,
):
    """Capture every matching Family once; regeneration binds the retained capture.

    The campaign-work transaction serializes the capture with mutation and purge
    producers, and the capture is one statement, never a paginated series that
    could mix a submission arriving between pages. File rendering stays
    asynchronous. `snapshot` is an internal service argument for regeneration,
    never accepted from the web form; with it, no query is accepted either,
    because the retained parameters, proof included, are the request.
    """
    if any(
        not isinstance(value, UUID) for value in (user_id, campaign_id, request_key)
    ):
        raise ValueError("Export identities must be canonical UUIDs.")
    if type(format) is not str or format not in {"csv", "xlsx", "pdf"}:
        raise ValueError("Unsupported financial export format.")
    if type(browser_timezone) is not str or browser_timezone not in timezone_names():
        raise ValueError("Export timezone must be an IANA name.")
    if snapshot is None:
        if not isinstance(query, FinancialQuery):
            raise ValueError("Financial export requires typed filters.")
    elif not isinstance(snapshot, FinancialExportSnapshot) or query is not None:
        raise ValueError("Regeneration cannot change the retained selection.")

    def admit(*args):
        """Both enqueue and its owning transaction recheck current authorization."""
        actor = authorize(store, user_id)
        if not allows(actor, Capability.FINANCIAL_DETAIL):
            raise PermissionError("Financial stewardship detail is unavailable.")
        admit_campaign(campaign_id, mutating=True)
        return True

    with work_transaction():
        admit()
        campaign = _campaign(campaign_id, capturing=snapshot is None)
        if snapshot is None:
            parameters = {
                # Re-parsed, so only the closed grammar's own values are retained.
                "filters": FinancialQuery.parse(query.form_values()).form_values(),
                # SQL honors the proof only for the snapshot and configuration it
                # then selects itself, so a concurrent change withholds money.
                "proof": giving_proof(campaign),
            }
        elif snapshot.campaign_id != campaign_id:
            raise ValueError("Retained financial capture belongs to another campaign.")
        else:
            parameters = snapshot.parameters
        previous = ExportRequest.objects.filter(
            requester_id=user_id, request_key=request_key
        ).first()
        if previous is not None:
            # A replay is the same selection; the proof may legitimately differ
            # when a promotion landed between two identical submissions.
            if (
                previous.report,
                previous.campaign_id,
                previous.parameters.get("filters"),
                previous.format,
                previous.browser_timezone,
                None if snapshot is None else previous.financial_snapshot_id,
            ) != (
                REPORT,
                campaign_id,
                parameters["filters"],
                format,
                browser_timezone,
                None if snapshot is None else snapshot.pk,
            ):
                raise ValueError("Export request identity is already bound.")
            return previous
        configuration_id = SystemConfiguration.objects.get().active_configuration_id
        correlation_id = uuid4()
        if snapshot is None:
            snapshot = FinancialExportSnapshot.objects.create(
                campaign_id=campaign_id,
                configuration_id=configuration_id,
                actor_id=user_id,
                correlation_id=correlation_id,
                parameters=parameters,
            )
            # The insert trigger, never client-supplied values, owns captured
            # data. Only the header comes back: the document itself, every
            # Family's money, is read by the render owner, never here.
            snapshot.refresh_from_db(fields=["created_at", "source", "row_count"])
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
            financial_snapshot=snapshot,
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


def export_document(request):
    """Word the retained capture the way the page words its live projection.

    Share wording for each row is versioned with the configuration the Family
    answered under, read from the campaign's retained versions; the summary,
    like the page's, can only use the campaign's current wording.
    """
    campaign = Campaign.objects.select_related("active_configuration").get(
        pk=request.campaign_id
    )
    parish_name = request.configuration.parish.name
    result = shape_result(
        request.financial_snapshot.document,
        campaign_id=request.campaign_id,
        parish_name=parish_name,
        configuration=campaign.active_configuration.values,
    )
    return financial_document(
        result,
        request.parameters,
        parish_name=parish_name,
        requested_at=request.created_at,
        timezone=request.browser_timezone,
    )
