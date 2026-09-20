"""Complete Ministry captures using the existing fenced export lifecycle."""

from uuid import UUID, uuid4

from parishkit.stewardship.accounts.policy import Capability, allows, current_principal
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.audit.schemas import Action, Outcome
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.jobs.storage import enqueue
from parishkit.stewardship.schema_primitives import timezone_names

from .export_models import ExportRequest, MinistryExportSnapshot
from .export_services import TASK_TYPE, admit_campaign, audit, authorize
from .ministries import MinistryQuery, can_report

MAX_PACKET_MINISTRIES = 200


def packet_parameters(ministries, *, history):
    """The packet's only choices: its Ministries and the history option.

    `None` means every Ministry the requester may see when SQL captures it.
    An explicit selection is canonical: distinct, ascending, positive DUIDs.
    Every other report filter holds its neutral value, as SQL also requires.
    """
    if type(history) is not bool or not (
        ministries is None
        or (
            type(ministries) is tuple
            and 1 <= len(ministries) <= MAX_PACKET_MINISTRIES
            and all(type(value) is int and 0 < value < 2**31 for value in ministries)
            and ministries == tuple(sorted(set(ministries)))
        )
    ):
        raise ValueError("Ministry packet requires a canonical selection.")
    query = MinistryQuery(history="all" if history else "current")
    return {
        "filters": query.form_values(),
        "ministry": None,
        "action": "packet",
        "ministries": None if ministries is None else list(ministries),
    }


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
    action="summary",
    snapshot=None,
    ministries=None,
    history=False,
):
    """Capture complete results atomically, or bind a trusted retained capture.

    SQL derives the source, privacy projection and exact authorized Ministry set.
    Neither HTTP fields nor a caller-supplied document choose those authorities.
    """
    if any(
        not isinstance(value, UUID) for value in (user_id, campaign_id, request_key)
    ):
        raise ValueError("Export identities must be canonical UUIDs.")
    if type(format) is not str or format not in {"csv", "xlsx", "pdf"}:
        raise ValueError("Unsupported Ministry export format.")
    if type(browser_timezone) is not str or browser_timezone not in timezone_names():
        raise ValueError("Export timezone must be an IANA name.")
    if snapshot is not None:
        if (
            not isinstance(snapshot, MinistryExportSnapshot)
            or snapshot.campaign_id != campaign_id
            or query is not None
            or ministry_id is not None
            or action != "summary"
            or ministries is not None
            or history
        ):
            raise ValueError("Regeneration cannot change retained selection.")
        parameters = snapshot.parameters
    elif action == "packet":
        if query is not None or ministry_id is not None:
            raise ValueError("A Ministry packet has no report filters.")
        parameters = packet_parameters(ministries, history=history)
    else:
        if ministries is not None or history:
            raise ValueError("Only a Ministry packet selects several Ministries.")
        if (
            not isinstance(query, MinistryQuery)
            or action not in {"summary", "join", "leave"}
            or (ministry_id is None) != (action == "summary")
            or (
                ministry_id is not None
                and (type(ministry_id) is not int or not 0 < ministry_id < 2**31)
            )
        ):
            raise ValueError("Ministry export requires typed selection.")
        query = MinistryQuery.parse(query.form_values(), detail=ministry_id is not None)
        parameters = {
            "filters": query.form_values(),
            "ministry": ministry_id,
            "action": action,
        }

    def admit(*args):
        """Reload coherent policy and gates under the owning work transaction."""
        actor = current_principal(store, user_id)
        if (
            not can_report(actor)
            or (
                snapshot is not None
                and not scope_authorized(actor, snapshot.authorization_scope)
            )
            or any(
                not allows(actor, Capability.MINISTRY_REPORT, ministry_id=value)
                for value in (
                    *(() if ministry_id is None else (ministry_id,)),
                    *(ministries or ()),
                )
            )
        ):
            raise PermissionError("Ministry export is unavailable.")
        admit_campaign(campaign_id, mutating=True)
        if ministries:
            # SQL refuses a selection it cannot resolve exactly. Say so here as a
            # bad selection, so a stale or crafted form is not reported as an
            # outage to retry. The form offers only this campaign's Ministries.
            offered = Campaign.objects.select_related("active_configuration").get(
                pk=campaign_id
            )
            if not set(ministries) <= set(
                offered.active_configuration.values.get("ministry_duids", ())
            ):
                raise ValueError("A selected Ministry is not in this campaign.")
        return True

    with work_transaction():
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
            ) != ("ministry", campaign_id, parameters, format, browser_timezone) or (
                snapshot is not None and previous.ministry_snapshot_id != snapshot.pk
            ):
                raise ValueError("Export request identity is already bound.")
            return previous
        configuration_id = SystemConfiguration.objects.get().active_configuration_id
        correlation_id = uuid4()
        if snapshot is None:
            snapshot = MinistryExportSnapshot.objects.create(
                campaign_id=campaign_id,
                configuration_id=configuration_id,
                actor_id=user_id,
                correlation_id=correlation_id,
                parameters=parameters,
            )
            snapshot.refresh_from_db(
                fields=["source", "row_count", "authorization_scope"]
            )
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
