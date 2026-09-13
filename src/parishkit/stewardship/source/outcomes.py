"""Durable completion and drained-read proof for the source worker's recovery.

These checks perform no provider I/O, mutate no source snapshot and grant no
new-work exemption. A completed immutable observation may be acknowledged even
after configuration changes; an unpromoted read needs safe drainage and fresh
request admission before it can be retried.
"""

from functools import cached_property

from django.db.models import F

from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.campaigns.work_locks import require_work_order
from parishkit.stewardship.jobs.admission import _scope
from parishkit.stewardship.jobs.dispatch import RecoveryPlan
from parishkit.stewardship.jobs.models import NONTERMINAL_STATES, TaskRun
from parishkit.stewardship.jobs.ownership import database_now
from parishkit.stewardship.jobs.storage import TaskStatus
from parishkit.stewardship.storage import StaleRecordError, StorageInvariantError

from .canonical import InvalidSourcePayload, canonical_payload
from .models import SourceCurrent, SourceMutationLease
from .refresh_models import (
    SourceRefreshAttempt,
    SourceRefreshFallback,
    SourceRefreshRequest,
)
from .requests import TASK_TYPE, _organization, _window, admit_refresh_request

MAX_AUTOMATIC_ATTEMPTS = 5


def retry_delay(attempt):
    """One capped exponential delay for live failures and abandoned read recovery."""
    if type(attempt) is not int or attempt < 1:
        raise ValueError("A positive source attempt is required.")
    return min(30 * 2 ** min(attempt - 1, 5), 600)


def failure_action(attempt, *, retry, contention=False):
    """Contention does not consume the bounded provider-failure retry allowance."""
    retry_delay(attempt)
    if type(retry) is not bool or type(contention) is not bool:
        raise TypeError("Source failure classification requires boolean flags.")
    return (
        "retryable_failure"
        if retry and (contention or attempt < MAX_AUTOMATIC_ATTEMPTS)
        else "permanent_failure"
    )


def scope_fingerprint(organization_id, window_digest):
    """Audit scope changes without omitting tenant identity or storing provider data."""
    return canonical_payload(
        {"organization_id": organization_id, "window_digest": window_digest}
    )[1]


def _request(status):
    """Verify the caller's exact current Task view under the shared work order."""
    require_work_order()
    if not isinstance(status, TaskStatus) or status.task_type != TASK_TYPE:
        raise PermissionError("This task does not own source refresh outcomes.")
    if (
        not TaskRun.objects.select_for_update()
        .filter(
            pk=status.run_id,
            root_id=status.root_id,
            task_type=TASK_TYPE,
            domain_request_id=status.domain_request_id,
            version=status.version,
            fence=status.fence,
            state=status.state,
            attempt=status.attempt,
            worker_id=status.worker_id,
        )
        .exists()
    ):
        raise StaleRecordError("Source outcome requires the current Task view.")
    request = SourceRefreshRequest.objects.filter(
        pk=status.domain_request_id,
        task_root_id=status.root_id,
    ).first()
    if request is None:
        raise PermissionError("Source outcome request binding is unavailable.")
    return request


def _attempts(request):
    """Only concretely bound attempts in this exact immutable request/retry root."""
    return SourceRefreshAttempt.objects.filter(
        request=request,
        task__root_id=request.task_root_id,
        snapshot__task_id=F("task_id"),
        snapshot__organization_id=request.organization_id,
        snapshot__kind=request.kind,
    )


def _direct_completion(request):
    """A full observation or completed delta must belong to this exact request."""
    return (
        _attempts(request)
        .filter(
            snapshot__state="promoted",
            snapshot__cursor__window_digest=request.window_digest,
        )
        .order_by("snapshot__generation")
        .values_list("snapshot_id", flat=True)
        .first()
    )


def _fallback(request):
    """SQL admits only delta-to-full, same-scope edges, so traversal is one level."""
    return (
        SourceRefreshFallback.objects.select_related("command__request")
        .filter(request=request)
        .first()
    )


class _Evidence:
    """One read-only admission's proof, never retained across a task mutation.

    The caller holds the work-order lock and `_request` locks the exact task
    version. Lazy values avoid duplicate SQL in composed recovery predicates;
    no process-global or request-lifetime authorization cache is involved.
    """

    def __init__(self, status):
        """Verify the immutable request binding once for this decision."""
        self.request = _request(status)

    @cached_property
    def fallback(self):
        """Resolve the optional single-level dependency only when needed."""
        return _fallback(self.request)

    @cached_property
    def completion(self):
        """Reuse exact promoted evidence throughout this read-only decision."""
        result = _direct_completion(self.request)
        if result is not None:
            return result
        return (
            None
            if self.fallback is None
            else _direct_completion(self.fallback.command.request)
        )

    @cached_property
    def dependency(self):
        """Read the linked full root's disposition once."""
        return _fallback_state(self.fallback)

    @cached_property
    def lease(self):
        """Lock source ownership once; wall-clock deadline checks remain live."""
        return SourceMutationLease.objects.select_for_update().get(singleton=True)

    @cached_property
    def drained(self):
        """Prove drainage at this decision's database instant."""
        return _read_attempts_drained(self.request, self.lease)

    @cached_property
    def superseding(self):
        """A promoted observation always takes precedence over changed scope."""
        return (
            None if self.completion is not None else _superseding_digest(self.request)
        )


def completed_snapshot(status):
    """Return own or explicitly linked full-fallback promoted evidence.

    Historical manifests remain proof after corpus compaction or a newer current
    pointer. A harmless delay before Task completion must not trigger another
    provider scan or lose acknowledgement when campaign configuration changes.
    """
    return _Evidence(status).completion


def fallback_state(status):
    """Resolve real dependency proof before consulting the target's latest retry run."""
    return _Evidence(status).dependency


def _fallback_state(fallback):
    """Resolve an already-bound fallback without repeating parent admission."""
    if fallback is None:
        return None
    target = fallback.command.request
    if _direct_completion(target) is not None:
        return "complete"
    state = (
        TaskRun.objects.filter(root_id=target.task_root_id)
        .order_by("-retry_sequence")
        .values_list("state", flat=True)
        .first()
    )
    if state in NONTERMINAL_STATES:
        return "pending"
    if state in {"failed", "cancelled"}:
        return state
    raise StorageInvariantError("Full fallback lacks a verified dependency outcome.")


def read_attempts_drained(status):
    """Prove old read leases/deadlines expired or a later safe source owner took over.

    A request with no attempt could not use the attempt-required HTTP transport.
    The highest source fence covers all earlier attempts because acquisition
    always waits for the predecessor's lease and reserved read/drain deadline.
    This conservative check does not infer drainage from process disappearance.
    """
    return _Evidence(status).drained


def _read_attempts_drained(request, lease):
    """Compare the retained attempt fence with one locked source lease."""
    fence = (
        _attempts(request)
        .order_by("-snapshot__source_fence")
        .values_list("snapshot__source_fence", flat=True)
        .first()
    )
    if fence is None:
        # There was no HTTP, but an acquired source reservation may still block
        # the retry. Do not burn automatic attempts on its unexpired old lease.
        if (
            lease.owner_id is None
            or not TaskRun.objects.filter(
                pk=lease.owner_id, root_id=request.task_root_id
            ).exists()
        ):
            return True
        fence = lease.fence
    if lease.fence < fence:
        raise StorageInvariantError("Source ownership predates retained attempt proof.")
    if lease.fence > fence:
        return True
    now = database_now()
    return not any(
        deadline is not None and deadline > now
        for deadline in (lease.expires_at, lease.external_deadline)
    )


def recovery_plan(status):
    """Recover abandoned read-only work, with bounded retries and fresh admission.

    Superseded-window cancellation requires a different actual immutable window,
    not merely denied admission. A pending fallback remains held without burning
    attempts; only the full dependency's real outcome can satisfy it.
    No provider access is enabled by this internal recovery decision alone.
    """
    return _recovery_plan(status, _Evidence(status))


def _recovery_plan(status, evidence):
    """Compose recovery checks using only this call's locked immutable evidence."""
    if not isinstance(status, TaskStatus) or status.state != "abandoned":
        raise PermissionError("Source recovery requires an abandoned Task.")
    if evidence.completion is not None:
        return RecoveryPlan("recovery_complete")
    if not evidence.drained:
        return None
    if evidence.superseding is not None:
        return RecoveryPlan("recovery_cancel")
    dependency = evidence.dependency
    if dependency == "pending":
        return None
    if dependency in {"failed", "cancelled"}:
        return RecoveryPlan(
            "recovery_fail" if dependency == "failed" else "recovery_cancel"
        )
    try:
        admit_refresh_request("recovery", status)
    except PermissionError:
        return None
    if status.attempt >= MAX_AUTOMATIC_ATTEMPTS:
        return RecoveryPlan("recovery_fail")
    return RecoveryPlan("recovery_retry", retry_delay(status.attempt))


def superseding_digest(status):
    """Identify coherent replacement scope, not temporary or inconsistent holds.

    Missing or inconsistent applied state cannot authorize cancellation. This
    metadata check does not admit new source work through a restore/purge gate.
    A promoted result takes precedence: it must be acknowledged, not cancelled.
    """
    return _Evidence(status).superseding


def _superseding_digest(request):
    """Compare current scope after the caller has ruled out promoted evidence."""
    runtime = SystemConfiguration.objects.select_for_update().first()
    if runtime is None or runtime.active_configuration_id is None:
        return None
    try:
        scope = _scope(runtime.current_campaign_id)
        organization = _organization(scope)
        window = _window(scope)
    except (PermissionError, InvalidSourcePayload):
        return None
    if (
        organization != request.organization_id
        or window.digest != request.window_digest
    ):
        return scope_fingerprint(organization, window.digest)
    return None


def admit_refresh_metadata(action, status):
    """Compiled recovery/completion admission, never a generic terminal-state permit."""
    evidence = _Evidence(status)
    if action in {"hint", "claim", "recovery_hint"}:
        dependency = evidence.dependency
        if dependency == "pending" and not (
            action == "recovery_hint" and status.state == "running"
        ):
            if action != "claim":
                return False
            raise PermissionError("Source request is waiting for its full fallback.")
        if action in {"hint", "claim"} and dependency in {"failed", "cancelled"}:
            return evidence.drained
    if action == "recovery_hint" and status.state == "abandoned":
        # A held abandoned root needs no broker publication until recovery can
        # act. Running expired claims must still be hinted so they get fenced.
        return _recovery_plan(status, evidence) is not None
    if action in {"lease_expired", "recovery_hint"}:
        # SQL proves actual expiry. Fencing an expired claim is bookkeeping,
        # so a changed campaign cannot indefinitely prevent abandonment.
        return True
    if action in {"complete", "recovery_complete"}:
        if evidence.completion is not None:
            return True
    elif action in {"recovery_retry", "recovery_fail", "recovery_cancel"}:
        plan = _recovery_plan(status, evidence)
        if plan is not None and plan.action == action:
            return True
    elif action == "safe_cancel":
        if (
            evidence.superseding is not None or evidence.dependency == "cancelled"
        ) and evidence.drained:
            return True
    elif action == "permanent_failure":
        if evidence.dependency == "failed" and evidence.drained:
            return True
    elif action == "retryable_failure" and evidence.dependency == "pending":
        lease = evidence.lease
        if (
            lease.owner_id is None
            or not TaskRun.objects.filter(
                pk=lease.owner_id, root_id=status.root_id
            ).exists()
        ):
            return True
    elif action in {"claim", "hint"} and evidence.completion is not None:
        # A consumer may claim only to acknowledge completed historical work.
        # Its ordinary effect and HTTP paths still require current admission.
        return True
    elif action in {"hint", "claim", "heartbeat", "progress", "effect"}:
        admit_refresh_request(action, status)
        if action in {"hint", "claim"}:
            request = evidence.request
            if (
                request.kind == "delta"
                and SourceCurrent.objects.get(singleton=True).snapshot_id is None
            ):
                # No-base fallback only queues a full dependency; it needs no
                # source reservation, credential or external observation.
                return True
            lease = evidence.lease
            now = database_now()
            # Do not burn attempts while another owner or its drain window is
            # known to prevent acquisition. A later claim/acquire race still
            # uses the executor's explicit contention-wait settlement.
            return not any(
                deadline is not None and deadline > now
                for deadline in (lease.expires_at, lease.external_deadline)
            )
        return True
    raise PermissionError("Source metadata transition lacks owning outcome proof.")
