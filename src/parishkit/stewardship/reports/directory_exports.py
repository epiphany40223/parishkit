"""Complete source captures through the existing requester-authorized export owner."""

from uuid import UUID, uuid4

from parishkit.stewardship.accounts.cryptography import CodeMacKeyring
from parishkit.stewardship.accounts.policy import Capability
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.audit.schemas import Action, Outcome
from parishkit.stewardship.campaigns.credential_keys import key_set_lock
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.jobs.storage import enqueue
from parishkit.stewardship.schema_primitives import timezone_names

from .directories import DirectoryQuery, selection_parameters
from .export_models import DirectoryExportSnapshot, ExportRequest
from .export_services import TASK_TYPE, admit_campaign, audit, authorize

REPORTS = frozenset({"family_directory", "postal_outreach"})


def create_directory_export(
    store,
    user_id,
    *,
    campaign_id,
    format,
    browser_timezone,
    request_key,
    query=None,
    postal=None,
    mac=None,
    snapshot=None,
):
    """Capture all matches once; regeneration reuses the trusted retained snapshot.

    The SQL insert trigger owns capture and source/row-count selection in one
    statement. The work lock serializes request allocation with campaign changes;
    code lookup holds its inventory lock, then persists only the stable Family
    selection. The web form can never supply a snapshot, Family ID or document.
    """
    if any(
        not isinstance(value, UUID) for value in (user_id, campaign_id, request_key)
    ):
        raise ValueError("Export identities must be canonical UUIDs.")
    if type(format) is not str or format not in {"csv", "xlsx", "pdf"}:
        raise ValueError("Unsupported directory export format.")
    if type(browser_timezone) is not str or browser_timezone not in timezone_names():
        raise ValueError("Export timezone must be an IANA name.")
    if snapshot is not None:
        if (
            not isinstance(snapshot, DirectoryExportSnapshot)
            or snapshot.campaign_id != campaign_id
        ):
            raise ValueError("Retained directory scope differs from the request.")
        if query is not None or postal is not None or mac is not None:
            raise ValueError("Regeneration cannot change retained selection.")
    elif (
        not isinstance(query, DirectoryQuery)
        or type(postal) is not bool
        or not isinstance(mac, CodeMacKeyring)
    ):
        raise ValueError("Directory export requires typed filters and kind.")

    def admit(*args):
        """Owning work and enqueue both check current requester and campaign gates."""
        authorize(store, user_id)
        admit_campaign(campaign_id, mutating=True)
        return True

    with work_transaction():
        admit()
        if snapshot is None:
            with key_set_lock(mac):
                parameters = selection_parameters(
                    campaign_id, query, postal=postal, mac=mac
                )
        else:
            parameters = snapshot.parameters
        report = "postal_outreach" if parameters["postal"] else "family_directory"
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
            ) != (report, campaign_id, parameters, format, browser_timezone) or (
                snapshot is not None and previous.directory_snapshot_id != snapshot.pk
            ):
                raise ValueError("Export request identity is already bound.")
            return previous
        configuration_id = SystemConfiguration.objects.get().active_configuration_id
        correlation_id = uuid4()
        if snapshot is None:
            snapshot = DirectoryExportSnapshot.objects.create(
                campaign_id=campaign_id,
                configuration_id=configuration_id,
                actor_id=user_id,
                correlation_id=correlation_id,
                parameters=parameters,
            )
            # Do not load the complete private document into the request thread.
            snapshot.refresh_from_db(fields=["source", "row_count"])
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
            directory_snapshot=snapshot,
            configuration_id=configuration_id,
            report=report,
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
