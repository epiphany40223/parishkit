"""Complete Ministry captures using the existing fenced export lifecycle."""

from uuid import UUID, uuid4

from django.db.models import F, Func, IntegerField, Q, TextField
from django.db.models.functions import Coalesce

from parishkit.stewardship.accounts.policy import Capability, allows, current_principal
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.audit.schemas import Action, Outcome
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.campaigns.work_locks import export_transaction
from parishkit.stewardship.jobs.storage import enqueue
from parishkit.stewardship.schema_primitives import timezone_names
from parishkit.stewardship.workflows.models import MinistryRequest

from .export_models import ExportRequest, MinistryExportSnapshot
from .export_services import (
    TASK_TYPE,
    ExportRequestBound,
    admit_campaign,
    audit,
    authorize,
)
from .ministries import MinistryQuery, can_report


def scope_authorized(principal, scope):
    """Retained operational columns cannot pass through a later role downgrade."""
    return allows(principal, Capability.MINISTRY_REPORT) or (
        scope.get("operational") is False
        and bool(scope.get("ministries"))
        and all(
            allows(principal, Capability.MINISTRY_REPORT, ministry_id=value)
            for value in scope["ministries"]
        )
    )


def offered_ministries(campaign_id):
    """The Ministry DUIDs the campaign offers for an export, as its forms do.

    The campaign's configured Ministries, and a Ministry removed from a live
    campaign that keeps current (not withdrawn) requests, which the report
    offers like any other (#342). A request is kept by its Ministry in
    force: the one an Edit carried it into, else the Family's (#2331), as
    the report SQL reads it. A request for a Member no longer in ParishSoft
    (#1217) does not keep one offered (#1219): the report SQL leaves it
    out, so this asks the same installed standing function, and only for
    the removed Ministries' requests. The command line's ``export
    ministry`` checks its Ministry against it.
    """
    offered = Campaign.objects.select_related("active_configuration").get(
        pk=campaign_id
    )
    configured = set(offered.active_configuration.values.get("ministry_duids", ()))
    kept = (
        MinistryRequest.objects.filter(
            submission__campaign_id=campaign_id, submission__mode="live"
        )
        .exclude(state__in=("cancelled", "superseded"))
        .annotate(
            held=Coalesce(
                "reviewed_ministry_duid", "ministry_duid", output_field=IntegerField()
            )
        )
        .exclude(held__in=configured)
        .annotate(
            standing=Func(
                F("pk"),
                function="stewardship_ministry_request_member_status_v1",
                output_field=TextField(),
            )
        )
        .filter(~Q(entity_kind="member") | Q(standing="current"))
    )
    return configured | set(kept.values_list("held", flat=True))


def create_ministry_export(
    store,
    user_id,
    *,
    campaign_id,
    format,
    browser_timezone,
    request_key,
    query=None,
    ministry_id=None,
    action=None,
):
    """Capture complete results atomically.

    SQL derives the source, privacy projection and exact authorized Ministry set.
    Neither HTTP fields nor a caller-supplied document choose those authorities.

    ``action`` is ``join`` or ``leave`` (one Ministry's list). The
    campaign-wide summary export was removed (#1126): its file held only
    the counts the page shows, so a new request for it is not accepted. The
    multi-Ministry Follow-up packet was removed too (#2394).
    """
    if any(
        not isinstance(value, UUID) for value in (user_id, campaign_id, request_key)
    ):
        raise ValueError("Export identities must be canonical UUIDs.")
    if type(format) is not str or format not in {"csv", "xlsx", "pdf"}:
        raise ValueError("Unsupported Ministry export format.")
    if type(browser_timezone) is not str or browser_timezone not in timezone_names():
        raise ValueError("Export timezone must be an IANA name.")
    if (
        not isinstance(query, MinistryQuery)
        or action not in {"join", "leave"}
        or type(ministry_id) is not int
        or not 0 < ministry_id < 2**31
    ):
        raise ValueError("Ministry export requires typed selection.")
    query = MinistryQuery.parse(query.form_values(), detail=True)
    parameters = {
        "filters": query.selection_filters(),
        "ministry": ministry_id,
        "action": action,
    }

    def admit(*args):
        """Reload coherent policy and gates under the owning work transaction."""
        actor = current_principal(store, user_id)
        if not can_report(actor) or not allows(
            actor, Capability.MINISTRY_REPORT, ministry_id=ministry_id
        ):
            raise PermissionError("Ministry export is unavailable.")
        admit_campaign(campaign_id, mutating=True)
        return True

    with export_transaction(campaign_id):
        admit()
        previous = ExportRequest.objects.filter(
            requester_id=user_id, request_key=request_key
        ).first()
        if previous is not None:
            authorize(store, user_id, request=previous)
            if (
                previous.report,
                previous.campaign_id,
                previous.parameters,
                previous.format,
                previous.browser_timezone,
            ) != ("ministry", campaign_id, parameters, format, browser_timezone):
                raise ExportRequestBound("Export request identity is already bound.")
            return previous
        configuration_id = SystemConfiguration.objects.get().active_configuration_id
        correlation_id = uuid4()
        snapshot = MinistryExportSnapshot.objects.create(
            campaign_id=campaign_id,
            configuration_id=configuration_id,
            actor_id=user_id,
            correlation_id=correlation_id,
            parameters=parameters,
        )
        snapshot.refresh_from_db(fields=["source", "row_count", "authorization_scope"])
        identifier = uuid4()
        task = enqueue(
            task_type=TASK_TYPE,
            domain_request_id=identifier,
            actor_id=user_id,
            correlation_id=correlation_id,
            idempotency_key=identifier,
            admit=admit,
        )
        result = ExportRequest.objects.create(
            id=identifier,
            campaign_id=campaign_id,
            requester_id=user_id,
            request_key=request_key,
            task_id=task.run_id,
            ministry_snapshot=snapshot,
            configuration_id=configuration_id,
            report="ministry",
            format=format,
            browser_timezone=browser_timezone,
            parameters=parameters,
            authorization_scope=snapshot.authorization_scope,
            actor_id=user_id,
            correlation_id=correlation_id,
        )
        audit(
            Action.EXPORT_REQUESTED,
            result,
            user_id,
            outcome=Outcome.STARTED,
            count=snapshot.row_count,
        )
        return result
