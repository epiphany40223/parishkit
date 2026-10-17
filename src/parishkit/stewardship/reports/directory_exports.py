"""Complete source captures through the existing requester-authorized export owner."""

from uuid import UUID, uuid4

from parishkit.stewardship.accounts.cryptography import CodeMacKeyring
from parishkit.stewardship.accounts.policy import Capability, allows
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.audit.schemas import Action, Outcome
from parishkit.stewardship.campaigns.credential_keys import key_set_lock
from parishkit.stewardship.campaigns.work_locks import export_transaction
from parishkit.stewardship.jobs.storage import enqueue
from parishkit.stewardship.schema_primitives import timezone_names

from .directories import DirectoryQuery, admit_mode, selection_parameters
from .export_models import DirectoryExportSnapshot, ExportRequest
from .export_services import (
    FAMILY_CODE_REPORTS,
    TASK_TYPE,
    ExportRequestBound,
    admit_campaign,
    audit,
    authorize,
)

REPORTS = FAMILY_CODE_REPORTS


def admit_directory(store, user_id, campaign_id, *, mode="production"):
    """The directory export's admission: the codes capability and campaign gates.

    The service's own check, inside its export lock; the command line's
    repeat of a bound request key runs it too (``admin_family_exports``).
    ``mode`` is the selection's: Testing rehearsal responses are for
    Administrators only, as on the page (#933).
    """
    principal = authorize(store, user_id)
    if not allows(principal, Capability.FAMILY_CODES):
        raise PermissionError("This export is unavailable.")
    admit_mode(principal, mode)
    admit_campaign(campaign_id, mutating=True)
    return True


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
    rehearsal=None,
):
    """Capture all matches once, as the page's export form does.

    The SQL insert trigger owns capture and source/row-count selection in one
    statement. The campaign's export lock serializes request allocation with
    that campaign's lifecycle and gate changes (not the global work order); code
    lookup holds its inventory lock, then persists only the stable Family
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
    if (
        not isinstance(query, DirectoryQuery)
        or type(postal) is not bool
        or not isinstance(mac, CodeMacKeyring)
    ):
        raise ValueError("Directory export requires typed filters and kind.")

    mode = query.mode

    def admit(*args):
        """Owning work and enqueue both check current requester and campaign gates."""
        return admit_directory(store, user_id, campaign_id, mode=mode)

    with export_transaction(campaign_id):
        admit()
        with key_set_lock(mac):
            parameters = selection_parameters(
                campaign_id, query, postal=postal, mac=mac, rehearsal=rehearsal
            )
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
            ) != (report, campaign_id, parameters, format, browser_timezone):
                raise ExportRequestBound("Export request identity is already bound.")
            return previous
        configuration_id = SystemConfiguration.objects.get().active_configuration_id
        correlation_id = uuid4()
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
