"""Shared exact/compiled authorization and audit; compiled request allocation."""

from datetime import timedelta
from uuid import UUID, uuid4

from django.db import connection, transaction

from parishkit.stewardship.accounts.policy import Capability, allows, current_principal
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.campaigns.work_locks import (
    export_transaction,
    work_transaction,
)
from parishkit.stewardship.jobs.ownership import database_now
from parishkit.stewardship.jobs.storage import enqueue, retry_failed
from parishkit.stewardship.schema_primitives import timezone_names

from .export_models import (
    ExportCancellation,
    ExportDownloadGrant,
    ExportDownloadUse,
    ExportPublication,
    ExportRequest,
    ParticipationExportSnapshot,
)
from .export_window import download_end, retry_end
from .facts import FactUnavailable
from .models import CampaignDailyFactSet
from .participation_summary import capture as summary_capture
from .retention import pin_facts

TASK_TYPE = "report_export"
# Directory exports carry every matching Family's code (and postal details),
# so they need the Family codes capability on top of campaign reporting.
FAMILY_CODE_REPORTS = frozenset({"family_directory", "postal_outreach"})


class ExportConflict(ValueError):
    """A valid command conflicts with an already completed export."""


class ExportRequestBound(ValueError):
    """A form's one-time request key was already used for a different export.

    Typically the page came back from the browser cache and was submitted again
    with another format. It is refused (never rebound) but, unlike malformed
    filters, the Admin can simply reload the page and submit again, so every
    export view answers it with 409 rather than the invalid-filters page.
    """


class ExportExpired(PermissionError):
    """The authorized requester asked to download an export whose file expired.

    Raised only after authorization succeeds, so the message discloses nothing
    to anyone else. It stays a PermissionError so a caller that does not know
    it still refuses; views that do know it show the expiry instead of the
    sign-in denial page.
    """


def authorize(store, user_id, *, request=None):
    """Reload current coherent policy; possession of an opaque UUID is not access."""
    principal = current_principal(store, user_id)
    permitted = allows(principal, Capability.CAMPAIGN_REPORT)
    # ExactExportRequest is a sibling model with no report column. Its callers
    # keep global capability policy, never compiled Ministry scope authority.
    if isinstance(request, ExportRequest) and request.report == "ministry":
        from .ministry_exports import scope_authorized

        permitted = scope_authorized(principal, request.authorization_scope)
    elif isinstance(request, ExportRequest) and request.report == "financial":
        # Money needs the page's own capability, not campaign reporting alone.
        permitted = permitted and allows(principal, Capability.FINANCIAL_DETAIL)
    elif isinstance(request, ExportRequest) and request.report == "family_timeline":
        # The full timeline is the page's view of Administrators and Staff
        # (#1774, #1799), as its SQL authorization (migration 0144) admits.
        permitted = permitted and bool({"administrator", "staff"} & principal.roles)
    elif isinstance(request, ExportRequest) and request.report == "campaign_archive":
        # The campaign archive holds each Family's giving: Past campaigns'
        # access, an Administrator with financial detail (#1018).
        permitted = (
            permitted
            and allows(principal, Capability.CONFIGURE)
            and allows(principal, Capability.FINANCIAL_DETAIL)
        )
    elif isinstance(request, ExportRequest) and request.report in FAMILY_CODE_REPORTS:
        # Codes need the directory page's own capability too, so a requester
        # who loses it can no longer download an earlier export.
        permitted = permitted and allows(principal, Capability.FAMILY_CODES)
        # Testing rehearsal responses are for Administrators only (#933).
        if directory_mode(request) == "testing":
            permitted = permitted and "administrator" in principal.roles
    elif isinstance(request, ExportRequest) and request.report == "family_test_names":
        # Real Family names from a chosen-Family test review (#817) need that
        # page's own capability, which only an Administrator holds.
        permitted = permitted and allows(principal, Capability.CONFIGURE)
    if not permitted or (
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


def directory_mode(request):
    """A directory export's response mode; captures before #933 are Production."""
    return (request.parameters or {}).get("mode", "production")


def audit(action, request, actor_id, *, outcome, count=None):
    """Retain non-sensitive result scope once, without copying filters or rows.

    SQL captured the filtered result Ministries separately from lifecycle scope.
    Copy only those numeric identifiers and the privacy projection into retained
    audit context, so attribution survives later campaign-detail purge. One
    event avoids per-Ministry chained writes for parish-wide exports.

    Ownership is the currently active Parish, derived in SQL, not the Parish of
    the configuration pinned when the export was requested: a later settings
    change would otherwise make every download and cancel fail attribution.
    """
    # The report kind and format (#1398): who exported what needs no request
    # row. A latest-data export is the participation report.
    context = {
        "outcome": outcome,
        "report": getattr(request, "report", "participation"),
        "format": request.format,
    }
    if count is not None:
        context["count"] = count
    # Exact-generation requests share this helper but have no Ministry payload.
    # A directory export names whose responses it holds (#933).
    if isinstance(request, ExportRequest) and request.report in FAMILY_CODE_REPORTS:
        context["report_mode"] = directory_mode(request)
    if isinstance(request, ExportRequest) and request.report == "ministry":
        context.update(
            ministry_duids=request.authorization_scope["result_ministries"],
            ministry_operational=request.authorization_scope["operational"],
        )
    record_action(
        action,
        actor_kind=ActorKind.PORTAL_USER,
        actor_id=actor_id,
        subject_id=request.pk,
        current_parish=True,
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
    with export_transaction(campaign_id):
        authorize(store, user_id)
        admit_campaign(campaign_id, mutating=True)
        previous = ExportRequest.objects.filter(
            requester_id=user_id, request_key=request_key
        ).first()
        if previous is not None:
            # An ended export's discard cleared its generation (#1398); the
            # same form replayed is still that export, whose page says it
            # is no longer available.
            if (
                previous.report,
                previous.campaign_id,
                previous.fact_set_id or fact_set_id,
                previous.format,
                previous.browser_timezone,
            ) != ("participation", campaign_id, fact_set_id, format, browser_timezone):
                raise ExportRequestBound("Export request identity is already bound.")
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
        # The page's Summary, frozen with the request (#1557, migration
        # 0142): the statistics and the funnel read now, in this
        # transaction, so the file holds what the page showed when asked.
        ParticipationExportSnapshot.objects.create(
            request=request,
            campaign_id=campaign_id,
            actor_id=user_id,
            correlation_id=correlation_id,
            document=summary_capture(campaign_id),
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
        # A replay of a recorded key returns its result whatever happened
        # since; a new Retry of a failed export is refused once its Retry
        # window is over (#1398). A cancelled or published export is
        # refused by retry_failed, as before (a conflict).
        if previous is None and task_ended(request.task):
            raise ExportExpired("This export's Retry window has ended.")
        latest = request.task.chain_runs.order_by("-retry_sequence").first()
        return retry_failed(
            run_id=previous.parent_id if previous is not None else latest.pk,
            command_id=request_key,
            actor_id=user_id,
            correlation_id=uuid4(),
            admit=lambda action, status: admit_export(action, status, store=store),
        )


def failed_end(task):
    """(latest run's state, its Retry end or None) for an unpublished export."""
    latest = task.chain_runs.order_by("-retry_sequence").first()
    end = retry_end(latest.updated_at) if latest.state == "failed" else None
    return latest.state, end


def task_ended(task):
    """Whether a failed export's task, never published, can no longer be
    retried: its latest run failed one window ago or more (#1398)."""
    latest = task.chain_runs.order_by("-retry_sequence").first()
    return latest.state == "failed" and retry_end(latest.updated_at) <= database_now()


def export_ended(request):
    """Whether ``request`` has ended, so its pages answer "no longer available".

    Exports are point-in-time (#1398; operations spec, "Point-in-time
    exports"): an export ends when its published file's window is over, when
    it was cancelled, or one window after its latest failed attempt if it
    has not been retried by then (``export_window``). The caller has already
    checked the reader's access to it (``authorize``).
    """
    publication = ExportPublication.objects.filter(request=request).first()
    if publication is not None:
        return download_end(publication) <= database_now()
    if ExportCancellation.objects.filter(request=request).exists():
        return True
    return task_ended(request.task)


def export_status(store, user_id, request_id):
    """Return only this authorized request, never another operational task's fields."""
    return export_state(store, user_id, request_id)[0]


def export_state(store, user_id, request_id):
    """This authorized request's status and its publication (None before one).

    The status is what ``export_status`` returns. The publication carries
    the stored file's size, SHA-256 and row count, which the command line's
    ``export status`` prints so ``export fetch`` can check what it wrote.
    """
    with transaction.atomic():
        request = ExportRequest.objects.get(pk=request_id)
        authorize(store, user_id, request=request)
        admit_campaign(request.campaign_id, mutating=False)
        publication = ExportPublication.objects.filter(request=request).first()
        # ``expires_at`` is when the export ends (export_window): a file's
        # download end, never a later recorded expires_at, or a failed
        # export's Retry end; None while it is being made or cancelled.
        end = None
        if publication is not None:
            end = download_end(publication)
            state = "ready" if end > database_now() else "expired"
        elif ExportCancellation.objects.filter(request=request).exists():
            state = "cancelled"
        else:
            state, end = failed_end(request.task)
        status = {
            "id": str(request.pk),
            "campaign_id": str(request.campaign_id),
            "report": request.report,
            "format": request.format,
            "state": state,
            "created_at": request.created_at.isoformat(),
            "expires_at": end.isoformat() if end is not None else None,
        }
        return status, publication


def issue_download(store, user_id, request_id):
    """Issue parish-owned security metadata, including during purge preparation."""
    with transaction.atomic():
        request = ExportRequest.objects.get(pk=request_id)
        authorize(store, user_id, request=request)
        admit_campaign(request.campaign_id, mutating=False)
        publication = ExportPublication.objects.get(request=request)
        if download_end(publication) <= database_now():
            raise ExportExpired("This export has expired.")
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
        if download_end(publication) <= database_now():
            raise ExportExpired("This export has expired.")
        if grant.expires_at <= database_now():
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
