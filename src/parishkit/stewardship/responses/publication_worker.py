"""The fenced ParishSoft publish worker for confirmed census plans (#528, BG-09).

``publish_plan`` writes one Administrator-confirmed publication plan
(``responses.publication_plans``) to ParishSoft, one Member at a time, under
``SourceMutationLease`` in its ``publication`` phase. It is safe by
construction:

- it refuses before any lease or call unless the ``parishsoft_publication``
  deployment setting is on (off by default; see
  docs/guides/stewardship-member-requests.md#parishsoft-publication), the
  system is in Production mode, and the plan is confirmed and not from
  Testing mode;
- right before each PUT it reads the Member's contact record uncached,
  compares its SHA-256 with the confirmed ``read_sha256``, rebuilds the plan
  from the current proposals and requires ``matches_confirmed``, then checks
  that the system is still in Production mode; any difference records
  ``invalidated`` without a write (a switch to Testing stops the whole run);
- it queues the entity's written proposals (``pending|failed -> queued``)
  with their rows locked ``FOR NO KEY UPDATE`` in pk order, under the fence,
  and reserves the lease's request window before every provider call;
- after the PUT it reads the record back and requires the confirmed
  ``planned_sha256`` before recording ``published`` and moving the
  proposals ``queued -> published``; anything else records ``failed`` with
  a sanitized word, and the entity is tried again (at most ``ATTEMPTS``
  times in a run); a published entity is never written again;
- an entity whose proposals are queued and whose fresh read already equals
  the planned payload (a crash after its PUT) is verified and recorded
  ``published`` without a second PUT;
- when the run ends, every entity still unfinished is recorded
  ``invalidated`` and its queued proposals go back to ``failed`` (an
  attempt failed) or ``pending`` (none did), so a new preflight can plan
  them and they never block a later confirmation; plans left unfinished by
  a crashed run are treated the same once ``EXECUTION_WINDOW`` has passed.

SQL checks the same rules independently (``schema/migrations/0090``: the
derived guard admits each proposal state change only as the confirmed plan
and its outcomes say). Each provider request has a hard deadline; the real
gateway's transport kills a request at it and logs ``helper_timed_out``
with what, the limit and the elapsed time. This module only drives a
``ContactGateway``; it never builds one, so CI runs it on a fake.
"""

from dataclasses import dataclass
from datetime import timedelta

from django.db.models import F
from django.db.models.functions import Now

from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.source.leases import (
    acquire_source,
    release_source,
    reserve_source_request,
    verify_source,
)

from .models import ProposedChange
from .publication import Outcome, payload_digest, plan_entity
from .publication_gateway import ContactGatewayError
from .publication_models import (
    PublicationConfirmation,
    PublicationEntity,
    PublicationOutcome,
)
from .publication_plans import matches_confirmed, record_outcome

# Write attempts per entity in one run (the spec's bounded shared retries).
ATTEMPTS = 3
# How long after its confirmation a plan may still be written; after that
# its unfinished entities are invalidated (Administrator default, #528).
EXECUTION_WINDOW = timedelta(hours=24)
# Each provider request's deadline; the lease's request window adds a
# safety margin to it.
REQUEST_SECONDS = 30
FINAL = ("published", "invalidated")


class PublicationDisabled(RuntimeError):
    """The ``parishsoft_publication`` setting is off (the default)."""


class PublicationRefused(RuntimeError):
    """The plan cannot be published now (Testing mode, or not confirmed)."""


@dataclass(frozen=True)
class RunResult:
    """What one run recorded: final outcomes by kind and failed attempts."""

    published: int = 0
    invalidated: int = 0
    failed_attempts: int = 0


def _production():
    """Whether the system is in Production mode right now."""
    return SystemConfiguration.objects.filter(mode="production").exists()


def _unfinished(plan_ids):
    """Entities with something to write and no final outcome, in pk order."""
    return list(
        PublicationEntity.objects.filter(
            plan_id__in=plan_ids, planned_sha256__isnull=False
        )
        .exclude(outcomes__result__in=FINAL)
        .order_by("pk")
    )


class _Unqueued:
    """A proposal this plan queued, seen as it was planned.

    Queueing advanced the version by one and set ``queued``; the planner and
    ``matches_confirmed`` compare against the plan's recorded state, which
    planned it as pending (or failed, which plans the same way).
    """

    execution = "pending"

    def __init__(self, row):
        """Wrap one queued proposal row."""
        self._row = row

    def __getattr__(self, name):
        """Every other attribute is the row's own."""
        return getattr(self._row, name)

    @property
    def version(self):
        """The version the plan recorded, before queueing advanced it."""
        return self._row.version - 1


class _Entity:
    """One confirmed entity's fields and the worker's view of its proposals."""

    def __init__(self, entity):
        """Load the entity's field rows once; they are immutable."""
        self.entity = entity
        self.fields = list(entity.planned_fields.all())
        self.recorded = {
            item.proposal_id: item.proposal_version for item in self.fields
        }
        self.writes = sorted(
            item.proposal_id for item in self.fields if item.outcome == Outcome.WRITE
        )

    def queued_by_plan(self, row):
        """Whether ``row`` is one of this plan's writes that it already queued."""
        return (
            row.pk in self.writes
            and row.execution == "queued"
            and row.version == self.recorded[row.pk] + 1
        )

    def rows(self):
        """The current proposals, queued ones shown as they were planned."""
        rows = ProposedChange.objects.filter(pk__in=self.recorded).order_by("pk")
        return [_Unqueued(row) if self.queued_by_plan(row) else row for row in rows]

    def landed(self, rows, fresh):
        """Whether this run's own earlier PUT is what ParishSoft now holds."""
        writes = [row for row in rows if row.pk in self.writes]
        return (
            fresh is not None
            and len(writes) == len(self.writes)
            and all(isinstance(row, _Unqueued) for row in writes)
            and payload_digest(fresh) == self.entity.planned_sha256
        )

    def _locked(self, ids):
        """Lock proposals ``FOR NO KEY UPDATE`` in pk order (inside a work txn)."""
        return list(
            ProposedChange.objects.select_for_update(no_key=True)
            .filter(pk__in=ids)
            .order_by("pk")
        )

    def queue(self):
        """Queue the written proposals; False, changing nothing, if any moved.

        Every planned proposal must still be at its recorded version, or be
        a write this plan already queued (a retry), so a decision, an
        Administrator edit or a later Family response since the plan stops
        the write.
        """
        rows = self._locked(self.recorded)
        if len(rows) != len(self.recorded) or not all(
            row.version == self.recorded[row.pk] or self.queued_by_plan(row)
            for row in rows
        ):
            return False
        self._move(
            [
                row.pk
                for row in rows
                if row.pk in self.writes and row.execution != "queued"
            ],
            "queued",
        )
        return True

    def settle(self, target):
        """Move the proposals this plan queued to ``target``; others stay as they are.

        A proposal a later Family response replaced meanwhile is no longer
        queued, so it is left alone; the outcome row still records the write.
        """
        rows = self._locked(self.writes)
        self._move([row.pk for row in rows if self.queued_by_plan(row)], target)

    @staticmethod
    def _move(ids, target):
        """Set ``execution`` and advance the version, which the guard requires."""
        ProposedChange.objects.filter(pk__in=ids).update(
            execution=target, version=F("version") + 1
        )


def _record(claim, item, result, failure_kind=""):
    """Record one outcome and settle the proposals it finishes, in one txn.

    ``published`` moves this plan's queued proposals to published;
    ``invalidated`` moves them back to failed (an attempt failed) or
    pending; ``failed`` leaves them queued for the next attempt.
    """
    with work_transaction():
        verify_source(claim)
        failed_before = PublicationOutcome.objects.filter(
            entity=item.entity, result="failed"
        ).exists()
        record_outcome(
            item.entity.pk, claim=claim, result=result, failure_kind=failure_kind
        )
        if result == "published":
            item.settle("published")
        elif result == "invalidated":
            item.settle("failed" if failed_before else "pending")


def _queue(claim, item):
    """Queue the entity's writes under the fence; False if anything moved."""
    with work_transaction():
        verify_source(claim)
        return item.queue()


def _read(claim, gateway, item):
    """One uncached contact read, inside a freshly reserved request window."""
    reserve_source_request(claim, timeout_seconds=REQUEST_SECONDS)
    return gateway.read_member_contact(item.entity.entity_key, timeout=REQUEST_SECONDS)


def _attempt(execution, claim, gateway, item):
    """One attempt on one entity: returns its final result, "failed" or "stop".

    "stop" means the system left Production mode; the caller invalidates
    every remaining entity.
    """
    execution.check()
    if not _production():
        return "stop"
    try:
        fresh = _read(claim, gateway, item)
    except ContactGatewayError as error:
        _record(claim, item, "failed", error.kind)
        return "failed"
    rows = item.rows()
    if not item.landed(rows, fresh):
        # ParishSoft must still hold exactly what preflight read, and the
        # proposals must plan exactly as confirmed (matches_confirmed also
        # re-checks both hashes, versions and outcomes).
        if fresh is None or payload_digest(fresh) != item.entity.read_sha256:
            _record(claim, item, "invalidated")
            return "invalidated"
        plan = plan_entity(item.entity.entity_kind, item.entity.entity_key, rows, fresh)
        if not matches_confirmed(item.entity, item.fields, plan, rows):
            _record(claim, item, "invalidated")
            return "invalidated"
        if not _production():
            return "stop"
        if not _queue(claim, item):
            _record(claim, item, "invalidated")
            return "invalidated"
        execution.check()
        try:
            reserve_source_request(claim, timeout_seconds=REQUEST_SECONDS)
            gateway.write_member_contact(
                item.entity.entity_key, plan.payload, timeout=REQUEST_SECONDS
            )
        except ContactGatewayError as error:
            _record(claim, item, "failed", error.kind)
            return "failed"
        try:
            fresh = _read(claim, gateway, item)
        except ContactGatewayError:
            _record(claim, item, "failed", "verify_unavailable")
            return "failed"
        if fresh is None or payload_digest(fresh) != item.entity.planned_sha256:
            _record(claim, item, "failed", "verify_mismatch")
            return "failed"
    _record(claim, item, "published")
    return "published"


def _confirmed_plan(plan_id):
    """The plan's confirmation, refusing a missing or Testing-mode one."""
    confirmation = (
        PublicationConfirmation.objects.select_related("plan")
        .filter(plan_id=plan_id)
        .first()
    )
    if confirmation is None or confirmation.plan.testing:
        raise PublicationRefused("This plan has no Production confirmation.")
    return confirmation


def _expired_plans():
    """Confirmed plans past their execution window, by the database clock."""
    return list(
        PublicationConfirmation.objects.filter(
            created_at__lte=Now() - EXECUTION_WINDOW
        ).values_list("plan_id", flat=True)
    )


def publish_plan(execution, plan_id, gateway, *, enabled):
    """Write one confirmed plan to ParishSoft through ``gateway``; see module.

    ``execution`` is the running publish task (its ``claim``, ``check``,
    ``effect`` and ``maintain_source``); ``enabled`` is the deployment's
    ``parishsoft_publication`` setting. Returns the run's ``RunResult``.
    """
    if enabled is not True:
        raise PublicationDisabled("ParishSoft publication is switched off.")
    if not _production():
        raise PublicationRefused("Publication stops after preflight in Testing mode.")
    _confirmed_plan(plan_id)
    with execution.effect():
        claim = acquire_source(
            task_id=execution.claim.run_id,
            task_fence=execution.claim.fence,
            worker_id=execution.claim.worker_id,
            phase="publication",
        )
    counts = {"published": 0, "invalidated": 0, "failed": 0}
    try:
        with execution.maintain_source(claim):
            expired = _expired_plans()
            # Abandoned work: earlier plans a crashed run left unfinished.
            for entity in _unfinished([pk for pk in expired if pk != plan_id]):
                _record(claim, _Entity(entity), "invalidated")
                counts["invalidated"] += 1
            stopped = plan_id in expired
            for entity in _unfinished([plan_id]):
                item = _Entity(entity)
                for _ in range(ATTEMPTS):
                    if stopped:
                        break
                    result = _attempt(execution, claim, gateway, item)
                    if result == "stop":
                        stopped = True
                    elif result in counts:
                        counts[result] += 1
                    if result != "failed":
                        break
            # Every entity this run did not finish is invalidated, so none
            # is left queued or blocking a later confirmation.
            for entity in _unfinished([plan_id]):
                _record(claim, _Entity(entity), "invalidated")
                counts["invalidated"] += 1
    finally:
        with work_transaction():
            release_source(claim)
    return RunResult(counts["published"], counts["invalidated"], counts["failed"])
