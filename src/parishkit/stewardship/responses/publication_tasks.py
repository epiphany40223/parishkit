"""The census publication tasks and how they are queued (#528, BG-09).

Two background task types run the publication that ``publication`` (the
planner), ``publication_plans`` (the plan record) and
``publication_worker`` (the fenced writer) implement:

- ``census_publication_preflight`` reads, without the source lease, each
  Member that has an open, approved Automatic change, plans each one and
  records the plan as the Administrator who asked for it (the data spec's
  preflight steps 2-7). Its ``domain_request_id`` is the plan's request
  key, so a repeated request or a rerun returns the same plan.
- ``census_publication`` runs ``publish_plan`` for one confirmed plan (its
  ``domain_request_id``).

Both run in the general worker on the source queue (the provider-bound
ParishSoft consumer, which refresh shares; publication also shares its
``SourceMutationLease``), and only the web login creates them (frozen
migration 0101's task-type maps). The web side is three calls, for the
later Publish page:

- ``request_preflight`` queues a preflight for an Administrator;
- ``confirm_and_publish`` records the Administrator's confirmation and
  queues its run in one transaction, so a confirmation never exists
  without its run;
- ``rerun_publication`` retries the plan's failed run while the plan still
  has an unfinished entity.

All three refuse while the deployment's ``parishsoft_publication`` setting
is off, and so do both tasks, before any ParishSoft call: a confirmed plan
whose run would refuse must never sit there blocking later confirmations.
The setting is off by default, and the real gateway's contact read refuses
until it is confirmed (``publication_gateway``), so nothing here can reach
ParishSoft yet.
"""

from functools import partial
from uuid import UUID

from django.db import DatabaseError

from parishkit.stewardship.accounts.policy import Capability, allows, current_principal
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.jobs.dispatch import Handler, RecoveryPlan
from parishkit.stewardship.jobs.models import NONTERMINAL_STATES, TaskRun
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.jobs.storage import enqueue, retry_failed
from parishkit.stewardship.observability import correlation
from parishkit.stewardship.reports.export_services import admit_campaign
from parishkit.stewardship.source.snapshot_models import SourceCurrent

from .accepted_base import with_accepted_base
from .models import ProposedChange
from .publication import PLANNABLE_EXECUTIONS, plan_entity
from .publication_gateway import ContactGatewayError
from .publication_models import PublicationConfirmation, PublicationPlan
from .publication_plans import confirm_plan, record_plan
from .publication_worker import (
    REQUEST_SECONDS,
    PublicationDisabled,
    PublicationRefused,
    check_publishable,
    publish_plan,
    unfinished,
)

PREFLIGHT_TASK_TYPE = "census_publication_preflight"
PUBLICATION_TASK_TYPE = "census_publication"
# Runs of one task before an abandoned one is left failed (an Administrator
# may then rerun a publication explicitly).
MAX_RUNS = 3
# How long an abandoned run waits before its retry: past the task lease and
# the source lease's request safety window, so the retry can take the lease.
RETRY_SECONDS = 120
# Actions that start new work, which need the task's full domain admission.
# Every other action (the scheduler's hints, heartbeats, finishing, failing
# and recovery) needs only the binding, so a run is always recorded as it
# ended and the scheduler, which has no grant on the plan tables, reads
# only the task row.
_NEW_WORK = frozenset({"enqueue", "explicit_retry", "claim", "effect"})


def _bound(status, task_type):
    """Refuse a task that is not ``task_type`` bound to its domain request."""
    if status.task_type != task_type or status.domain_request_id is None:
        raise PermissionError("The task does not own a census publication.")


def _current_campaign():
    """The current campaign's id, refusing when there is none."""
    campaign_id = SystemConfiguration.objects.values_list(
        "current_campaign_id", flat=True
    ).first()
    if campaign_id is None:
        raise PermissionError("Census publication needs a current campaign.")
    return campaign_id


def admit_preflight(action, status):
    """Domain admission for a preflight: its binding and a current campaign.

    Creating and claiming one needs a current campaign; a campaign that
    ends during the run fails it when it reads its scope.
    """
    _bound(status, PREFLIGHT_TASK_TYPE)
    if action in {"enqueue", "claim"}:
        _current_campaign()
    return True


def _other_runs(status):
    """Unfinished publish runs of the same plan, outside this task's root."""
    return (
        TaskRun.objects.filter(
            task_type=PUBLICATION_TASK_TYPE,
            domain_request_id=status.domain_request_id,
            state__in=NONTERMINAL_STATES,
        )
        .exclude(root_id=status.root_id)
        .exists()
    )


def admit_publication(action, status):
    """Domain admission for a publish run of one confirmed plan.

    New work needs a Production (not Testing) confirmation of the plan.
    Creating, retrying or claiming a run also needs no other unfinished run
    of the same plan, and an explicit retry needs an entity of the plan
    still unfinished.
    """
    _bound(status, PUBLICATION_TASK_TYPE)
    if action not in _NEW_WORK:
        return True
    plan_id = status.domain_request_id
    if not PublicationConfirmation.objects.filter(
        plan_id=plan_id, plan__testing=False
    ).exists():
        raise PermissionError("This plan has no Production confirmation.")
    if action in {"enqueue", "explicit_retry", "claim"} and _other_runs(status):
        raise PermissionError("Another run of this plan is unfinished.")
    if action == "explicit_retry" and not unfinished([plan_id]):
        raise PermissionError("Every change in this plan is finished.")
    return True


def _abandoned(status):
    """Refuse recovery of a task that is not abandoned."""
    if status.state != "abandoned":
        raise PermissionError("Recovery requires an abandoned task.")


def recover_preflight(status):
    """An abandoned preflight completes if its plan exists, else is retried.

    Preflight only reads ParishSoft, so a bounded retry is safe; the plan
    for its request key, once recorded, is the proof it finished.
    """
    _abandoned(status)
    actor_id = TaskRun.objects.values_list("initiated_by_id", flat=True).get(
        pk=status.run_id
    )
    if PublicationPlan.objects.filter(
        actor_id=actor_id, request_key=status.domain_request_id
    ).exists():
        return RecoveryPlan("recovery_complete")
    if status.attempt < MAX_RUNS:
        return RecoveryPlan("recovery_retry", RETRY_SECONDS)
    return RecoveryPlan("recovery_fail")


def recover_publication(status):
    """An abandoned publish run is retried on the same plan (#1245 note 2).

    A run that ended with an exception skipped closing its entities, and
    the overlap check would refuse a new plan for the same changes. Its
    retry reads before every PUT and recognizes a PUT that already landed,
    so it never writes twice. It completes once no entity is unfinished,
    and after ``MAX_RUNS`` it is failed, for an explicit rerun.
    """
    _abandoned(status)
    if not unfinished([status.domain_request_id]):
        return RecoveryPlan("recovery_complete")
    if status.attempt < MAX_RUNS:
        return RecoveryPlan("recovery_retry", RETRY_SECONDS)
    return RecoveryPlan("recovery_fail")


def _request_binding(execution):
    """The running task's requester and domain request, read under an effect."""
    with execution.effect():
        task = TaskRun.objects.get(pk=execution.claim.run_id)
        return task.initiated_by_id, task.domain_request_id


def approved_changes(campaign_id):
    """Every open, approved, Automatic Member change of the campaign.

    The changes a preflight plans, from the campaign's live responses; the
    Publish to ParishSoft page counts the same ones as waiting (#528).
    """
    return ProposedChange.objects.filter(
        submission__campaign_id=campaign_id,
        submission__mode="live",
        entity_kind="member",
        handling="api",
        decision="approved",
        execution__in=PLANNABLE_EXECUTIONS,
    )


def publication_running(campaign_id):
    """Whether a publish run of one of the campaign's plans is unfinished.

    The page's (and later the command line's) gate on Check ParishSoft: the
    preflight service itself does not refuse a check during a run.
    """
    return TaskRun.objects.filter(
        task_type=PUBLICATION_TASK_TYPE,
        state__in=NONTERMINAL_STATES,
        domain_request_id__in=PublicationPlan.objects.filter(
            campaign_id=campaign_id
        ).values("pk"),
    ).exists()


def _preflight_scope():
    """The campaign, promoted snapshot, organization and changes to plan.

    The changes are every open, approved, Automatic Member change from a
    live response in the current campaign; they are grouped by Member.
    """
    campaign_id = _current_campaign()
    current = SourceCurrent.objects.get(singleton=True)
    if current.snapshot_id is None or current.organization_id is None:
        raise PermissionError("Census publication needs promoted ParishSoft data.")
    # Annotated with each row's active accepted base (#528, ADM-09.03), so
    # a resolved conflict plans against the ParishSoft value accepted.
    rows = list(
        with_accepted_base(approved_changes(campaign_id)).order_by(
            "entity_key", "field", "pk"
        )
    )
    members = {}
    for row in rows:
        members.setdefault(row.entity_key, []).append(row)
    return campaign_id, current.snapshot_id, current.organization_id, rows, members


def _read_members(execution, gateway, keys):
    """Each Member's contact record, read outside any transaction.

    A Member whose read fails is None, so the planner marks it *cannot
    check* and blocks it. Returns None when the gateway says the contact
    read itself is not confirmed: then no plan can be made at all.
    """
    payloads = {}
    for key in keys:
        execution.check()
        try:
            payloads[key] = gateway.read_member_contact(key, timeout=REQUEST_SECONDS)
        except ContactGatewayError as error:
            if error.kind == "read_unconfirmed":
                return None
            payloads[key] = None
    return payloads


def _refuse(execution, action):
    """Record why the run is refused, then fail it for good.

    ``action`` is the refusal's own audit event (safety review L3), so the
    System log tells the switch being off apart from an unconfirmed contact
    read, and both from any other refusal. A crash between the two leaves
    the run abandoned; its recovery refuses again, recording it again.
    """
    with execution.effect():
        record_action(
            action,
            actor_kind=ActorKind.SYSTEM,
            subject_id=execution.claim.run_id,
            current_parish=True,
            context={"outcome": Outcome.FAILED},
        )
    execution.transition("permanent_failure")


def _refusal(error):
    """The audit event for a worker refusal: the switch off, or another."""
    if isinstance(error, PublicationDisabled):
        return Action.CENSUS_PUBLICATION_SWITCHED_OFF
    return Action.CENSUS_PUBLICATION_REFUSED


def _execute_preflight(execution, *, enabled, gateway):
    """Read, plan and record one preflight; see the module docstring.

    Every refusal is a permanent failure with no plan recorded, and the
    Administrator asks again: the switch off, no current campaign or
    promoted data, an unconfirmed contact read, or a change that moved
    since the read (the plan guard refuses a stale version). Each is
    audited with its reason (``_refuse``).
    """
    with correlation(execution.correlation_id):
        if enabled is not True:
            _refuse(execution, Action.CENSUS_PUBLICATION_SWITCHED_OFF)
            return
        actor_id, request_key = _request_binding(execution)
        with execution.effect():
            done = PublicationPlan.objects.filter(
                actor_id=actor_id, request_key=request_key
            ).exists()
            scope = None if done else _scope_or_none()
        if done:
            execution.transition("complete")
            return
        if scope is None:
            _refuse(execution, Action.CENSUS_PUBLICATION_REFUSED)
            return
        campaign_id, snapshot_id, organization_id, rows, members = scope
        source = gateway(execution)
        try:
            payloads = _read_members(execution, source, members)
        finally:
            source.close()
        if payloads is None:
            _refuse(execution, Action.CENSUS_PUBLICATION_READ_UNCONFIRMED)
            return
        plans = [
            plan_entity("member", key, group, payloads[key])
            for key, group in members.items()
        ]
        try:
            with execution.effect():
                record_plan(
                    actor_id,
                    campaign_id=campaign_id,
                    source_snapshot_id=snapshot_id,
                    organization_id=organization_id,
                    request_key=request_key,
                    plans=plans,
                    rows=rows,
                )
        except DatabaseError:
            _refuse(execution, Action.CENSUS_PUBLICATION_REFUSED)
            return
        execution.transition("complete")


def _scope_or_none():
    """``_preflight_scope``, or None when there is nothing it can plan from."""
    try:
        return _preflight_scope()
    except PermissionError:
        return None


def _execute_publication(execution, *, enabled, gateway):
    """Run ``publish_plan`` for the task's plan and record how the run ended.

    A refusal before any lease (the switch off, Testing mode, no Production
    confirmation) is a permanent failure, audited with its reason
    (``_refuse``), as is the same refusal inside the run. Otherwise every
    entity ends
    published or invalidated, so the run completes. An unexpected error
    propagates: the claim expires and recovery retries the same plan.
    """
    with correlation(execution.correlation_id):
        _, plan_id = _request_binding(execution)
        try:
            check_publishable(plan_id, enabled=enabled)
        except (PublicationDisabled, PublicationRefused) as error:
            _refuse(execution, _refusal(error))
            return
        source = gateway(execution)
        try:
            publish_plan(execution, plan_id, source, enabled=enabled)
        except (PublicationDisabled, PublicationRefused) as error:
            refusal = _refusal(error)
        else:
            refusal = None
        finally:
            source.close()
        if refusal is not None:
            _refuse(execution, refusal)
            return
        execution.transition("complete")


def parishsoft_gateway(credential_path):
    """Build the real ParishSoft gateway for one running task (#1245 note 4).

    It reads the installed credential at run time and records every request
    killed at its deadline with ``source_timeout_recorder``, as refresh
    does, so a kill is logged with what, the limit and the elapsed time.
    """

    def build(execution):
        """The gateway bound to ``execution``'s ownership checks."""
        from parishkit.stewardship.source.credentials import SourceCredential
        from parishkit.stewardship.source.transport import (
            runtime_profile,
            source_timeout_recorder,
        )

        from .publication_gateway import ParishSoftContactGateway

        return ParishSoftContactGateway.open(
            execution,
            SourceCredential.read(credential_path),
            profile=runtime_profile().value,
            on_timeout=source_timeout_recorder(execution),
        )

    return build


def preflight_handler(*, enabled, gateway):
    """The worker's preflight handler; ``gateway(execution)`` builds a gateway."""
    if type(enabled) is not bool or not callable(gateway):
        raise TypeError("Preflight requires the real setting and a gateway.")
    return Handler(
        queue=WorkQueue.SOURCE,
        admit=admit_preflight,
        execute=partial(_execute_preflight, enabled=enabled, gateway=gateway),
        recover=recover_preflight,
        scope=work_transaction,
    )


def publication_handler(*, enabled, gateway):
    """The worker's publish handler; ``gateway(execution)`` builds a gateway."""
    if type(enabled) is not bool or not callable(gateway):
        raise TypeError("Publication requires the real setting and a gateway.")
    return Handler(
        queue=WorkQueue.SOURCE,
        admit=admit_publication,
        execute=partial(_execute_publication, enabled=enabled, gateway=gateway),
        recover=recover_publication,
        scope=work_transaction,
    )


def scheduler_handlers(unavailable):
    """Metadata-only handlers for the scheduler, which hints both types."""
    return {
        PREFLIGHT_TASK_TYPE: Handler(
            queue=WorkQueue.SOURCE,
            admit=admit_preflight,
            execute=unavailable,
            recover=recover_preflight,
            scope=work_transaction,
        ),
        PUBLICATION_TASK_TYPE: Handler(
            queue=WorkQueue.SOURCE,
            admit=admit_publication,
            execute=unavailable,
            recover=recover_publication,
            scope=work_transaction,
        ),
    }


def _uuids(*values):
    """Whether every value is a real UUID."""
    return all(isinstance(value, UUID) for value in values)


def _administrator(store, actor_id):
    """Refuse anyone but an Administrator who may publish."""
    if not allows(current_principal(store, actor_id), Capability.PUBLISH_CENSUS):
        raise PermissionError("Publishing to ParishSoft is unavailable.")


def request_preflight(store, actor_id, *, request_key, correlation_id, enabled):
    """Queue one preflight for an Administrator, replay-safe; return its task.

    ``enabled`` is the web deployment's ``parishsoft_publication`` setting.
    The same ``request_key`` returns the task (and later the plan) already
    made for it. A new request is audited: who asked, and when.
    """
    if enabled is not True:
        raise PublicationDisabled("ParishSoft publication is switched off.")
    if not _uuids(actor_id, request_key, correlation_id):
        raise ValueError("Invalid publication preflight.")
    with work_transaction():
        _administrator(store, actor_id)
        campaign_id = _current_campaign()
        admit_campaign(campaign_id, mutating=True)
        # The work order serializes this check with the enqueue, so a replay
        # of the same key is never audited as a second request.
        replay = TaskRun.objects.filter(
            task_type=PREFLIGHT_TASK_TYPE, idempotency_key=str(request_key)
        ).exists()
        task = enqueue(
            task_type=PREFLIGHT_TASK_TYPE,
            domain_request_id=request_key,
            actor_id=actor_id,
            correlation_id=correlation_id,
            idempotency_key=request_key,
            admit=admit_preflight,
        )
        if not replay:
            _requested(
                Action.CENSUS_PUBLICATION_PREFLIGHT_REQUESTED,
                actor_id,
                task,
                campaign_id,
                Outcome.STARTED,
            )
        return task


def _requested(action, actor_id, task, campaign_id, outcome):
    """Audit an Administrator's request: who, when, and the task it made."""
    record_action(
        action,
        actor_kind=ActorKind.PORTAL_USER,
        actor_id=actor_id,
        subject_id=task.run_id,
        campaign_id=campaign_id,
        current_parish=True,
        context={"outcome": outcome},
    )


def confirm_and_publish(
    store, actor_id, plan_id, *, correlation_id, enabled, **confirmation
):
    """Confirm one plan and queue its run in one transaction, replay-safe.

    ``confirmation`` is ``confirm_plan``'s keywords (session, sign-in time,
    request key and the counts shown). The run's key is the confirmation,
    so a replay returns the same run. Returns ``(confirmation, task)``.
    """
    if enabled is not True:
        raise PublicationDisabled("ParishSoft publication is switched off.")
    if not _uuids(correlation_id):
        raise ValueError("Invalid publication confirmation.")
    with work_transaction():
        record = confirm_plan(store, actor_id, plan_id, **confirmation)
        task = enqueue(
            task_type=PUBLICATION_TASK_TYPE,
            domain_request_id=plan_id,
            actor_id=actor_id,
            correlation_id=correlation_id,
            idempotency_key=record.pk,
            admit=admit_publication,
        )
        return record, task


def rerun_publication(store, actor_id, plan_id, *, command_id, correlation_id, enabled):
    """Retry a plan's failed run while it has unfinished entities (note 2).

    The retry is the plan's own run again (``retry_failed``), never a new
    plan, so the overlap check that blocks a new plan for the same changes
    does not stop it. The same ``command_id`` returns the same retry; a new
    one is audited: who asked, and when.
    """
    if enabled is not True:
        raise PublicationDisabled("ParishSoft publication is switched off.")
    if not _uuids(actor_id, plan_id, command_id, correlation_id):
        raise ValueError("Invalid publication rerun.")
    with work_transaction():
        _administrator(store, actor_id)
        # One confirmation per plan, so its runs share one root.
        latest = (
            TaskRun.objects.filter(
                task_type=PUBLICATION_TASK_TYPE, domain_request_id=plan_id
            )
            .order_by("-retry_sequence")
            .first()
        )
        if latest is None:
            raise PermissionError("This plan has never been run.")
        previous = TaskRun.objects.filter(
            root_id=latest.root_id, retry_command_id=command_id
        ).first()
        if previous is None and not unfinished([plan_id]):
            raise PermissionError("Every change in this plan is finished.")
        task = retry_failed(
            run_id=latest.pk if previous is None else previous.parent_id,
            command_id=command_id,
            actor_id=actor_id,
            correlation_id=correlation_id,
            admit=admit_publication,
        )
        if previous is None:
            _requested(
                Action.CENSUS_PUBLICATION_RERUN_REQUESTED,
                actor_id,
                task,
                PublicationPlan.objects.values_list("campaign_id", flat=True).get(
                    pk=plan_id
                ),
                Outcome.RETRY,
            )
        return task
