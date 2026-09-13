"""Ordered durable refresh admission; no network work and no queue dependency."""

import re
from dataclasses import dataclass
from uuid import UUID, uuid4

from parishkit.stewardship.accounts.configuration_models import AppliedIntegration
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.jobs.admission import require_source_refresh
from parishkit.stewardship.jobs.models import NONTERMINAL_STATES, TaskRun
from parishkit.stewardship.jobs.storage import enqueue
from parishkit.stewardship.storage import StorageInvariantError

from .canonical import canonical_payload
from .models import SourceCurrent, SourceMutationLease, SourceSnapshot
from .refresh_models import REFRESH_CAUSES, SourceRefreshCommand, SourceRefreshRequest
from .windows import refresh_window

TASK_TYPE = "source_refresh"


@dataclass(frozen=True)
class RefreshReceipt:
    """Safe identities for status links; no credentials, query payload or PII."""

    command_id: UUID
    request_id: UUID
    task_root_id: UUID


def _window(scope):
    """Select only the locked current campaign, never archived history collections."""
    campaign = scope.campaign
    return refresh_window(
        campaign_id=None if campaign is None else campaign.pk,
        state=None if campaign is None else campaign.state,
        values=None if campaign is None else campaign.active_configuration.values,
    )


def _organization(scope):
    """Require a canonical configured v2 tenant and never replace another tenant."""
    settings = (
        AppliedIntegration.objects.filter(
            configuration_id=scope.runtime.active_configuration_id, kind="parishsoft"
        )
        .values_list("settings", flat=True)
        .first()
    )
    value = None if settings is None else settings.get("organization_id")
    if (
        type(value) is not str
        or re.fullmatch(r"[1-9][0-9]{0,9}", value) is None
        or int(value) > 2**31 - 1
    ):
        raise PermissionError("Source refresh requires its configured organization.")
    organization_id = int(value)
    current = SourceCurrent.objects.values_list("organization_id", flat=True).first()
    if current is not None and current != organization_id:
        raise PermissionError("The source organization differs from retained truth.")
    return organization_id


def admit_refresh_request(action, status):
    """Handler callback revalidates immutable binding for claim and every effect.

    This is domain admission, not Admin authorization. The compiled caller must
    enter work_transaction before its task lock. Completion and recovery need
    additional manifest proof in the concrete refresh handler.
    """
    if status.task_type != TASK_TYPE or status.domain_request_id is None:
        raise PermissionError("The task does not own a source refresh request.")
    request = SourceRefreshRequest.objects.filter(pk=status.domain_request_id).first()
    if request is None or request.task_root_id != status.root_id:
        raise PermissionError("The source refresh task binding is unavailable.")
    scope = require_source_refresh(campaign_id=request.campaign_id)
    if (
        _organization(scope) != request.organization_id
        or _window(scope).digest != request.window_digest
    ):
        raise PermissionError("The source refresh window is no longer current.")
    return True


def _pending(*, organization_id, digest, kind):
    """Coalesce only waiting work, never the poll currently executing.

    Running claims count as active even before acquiring SourceMutationLease.
    A retained lease and a committed promotion also exclude their retry root;
    this closes the release-to-task-completion interval. The global work order
    serializes creation with every compiled refresh claim/effect.
    """
    active_roots = TaskRun.objects.filter(state="running").values("root_id")
    leased_roots = SourceMutationLease.objects.filter(owner__isnull=False).values(
        "owner__root_id"
    )
    promoted_roots = SourceSnapshot.objects.filter(state="promoted").values(
        "task__root_id"
    )
    waiting_roots = TaskRun.objects.filter(state__in=NONTERMINAL_STATES).values(
        "root_id"
    )
    candidates = SourceRefreshRequest.objects.filter(
        organization_id=organization_id,
        window_digest=digest,
        task_root_id__in=waiting_roots,
    ).exclude(task_root_id__in=active_roots)
    candidates = candidates.exclude(task_root_id__in=leased_roots).exclude(
        task_root_id__in=promoted_roots
    )
    if kind == "full":
        candidates = candidates.filter(kind="full")
    # A waiting full load satisfies a delta request, never the other way round.
    return candidates.order_by("created_at", "id").first()


def _receipt(command):
    """Return only durable identifiers, including when the command is replayed."""
    return RefreshReceipt(command.pk, command.request_id, command.request.task_root_id)


def request_refresh(*, command_id, cause, actor_id, correlation_id, authorize):
    """Record one owning command, coalescing a full load after any active poll.

    The compiled producer supplies an explicit current authorization callback
    accepting the locked WorkScope and returning True. It runs even on replay;
    callers cannot treat a known command UUID as authority. Browser endpoints
    must authorize Admin here, not merely before entering this transaction.
    """
    if (
        not isinstance(command_id, UUID)
        or not isinstance(correlation_id, UUID)
        or (actor_id is not None and not isinstance(actor_id, UUID))
        or type(cause) is not str
        or cause not in REFRESH_CAUSES
        or (cause == "manual" and actor_id is None)
        or not callable(authorize)
    ):
        raise ValueError("A refresh requires canonical command and owner bindings.")
    kind = "delta" if cause == "delta" else "full"
    with work_transaction():
        campaign_id = SystemConfiguration.objects.values_list(
            "current_campaign_id", flat=True
        ).first()
        scope = require_source_refresh(campaign_id=campaign_id)
        if authorize(scope) is not True:
            raise PermissionError("The source refresh command is not authorized.")
        previous = (
            SourceRefreshCommand.objects.select_related("request")
            .filter(pk=command_id)
            .first()
        )
        if previous is not None:
            if (previous.actor_id, previous.cause, previous.kind) != (
                actor_id,
                cause,
                kind,
            ):
                raise StorageInvariantError("The refresh command is already bound.")
            return _receipt(previous)
        organization_id = _organization(scope)
        canonical, digest = canonical_payload(_window(scope).document())
        request = _pending(organization_id=organization_id, digest=digest, kind=kind)
        if request is None:
            request_id = uuid4()

            def admit_creation(action, status):
                """Creation's parent is inserted atomically immediately after root."""
                return (
                    action == "enqueue"
                    and status.task_type == TASK_TYPE
                    and status.domain_request_id == request_id
                    and authorize(scope) is True
                )

            task = enqueue(
                task_type=TASK_TYPE,
                domain_request_id=request_id,
                actor_id=actor_id,
                correlation_id=correlation_id,
                idempotency_key=request_id,
                admit=admit_creation,
            )
            request = SourceRefreshRequest.objects.create(
                id=request_id,
                task_root_id=task.root_id,
                configuration_id=scope.runtime.active_configuration_id,
                campaign_id=scope.runtime.current_campaign_id,
                organization_id=organization_id,
                kind=kind,
                window_canonical=canonical,
                window_digest=digest,
                actor_id=actor_id,
                correlation_id=correlation_id,
            )
        command = SourceRefreshCommand.objects.create(
            id=command_id,
            request=request,
            kind=kind,
            cause=cause,
            actor_id=actor_id,
            correlation_id=correlation_id,
        )
        return _receipt(command)
