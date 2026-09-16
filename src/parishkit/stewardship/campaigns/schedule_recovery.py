"""Pure, deterministic missed-work decisions shared by schedule producers.

These values are not admission evidence. The durable owner supplies one complete
locked group, rechecks mode/revision/Family/hold state, and atomically persists
the outcomes and semantic coverage before making the selected work dispatchable.
Large groups are staged by that owner; never run this on an arbitrary first page.
No function here renders content, retries terminal failure, or calls a provider.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from uuid import UUID

KINDS = frozenset({"initial", "reminder", "daily_digest", "weekly_digest"})
STATES = frozenset(
    {
        "pending",
        "running",
        "delivery_unknown",
        "succeeded",
        "failed",
        "skipped",
        "coalesced",
    }
)


@dataclass(frozen=True)
class RecoverySlot:
    """Nonprivate locked-input projection; cancellation proof remains with owner."""

    occurrence_id: UUID
    definition_id: UUID
    kind: str
    mode: str
    target: str
    slot: str
    due_at: datetime
    state: str = "pending"
    safely_cancellable: bool = False
    held: bool = False

    def __post_init__(self):
        """Reject coerced identities, unbounded keys and ambiguous clock inputs."""
        if (
            not isinstance(self.occurrence_id, UUID)
            or not isinstance(self.definition_id, UUID)
            or type(self.kind) is not str
            or self.kind not in KINDS
            or type(self.mode) is not str
            or self.mode not in {"testing", "production"}
            or type(self.state) is not str
            or self.state not in STATES
            or type(self.due_at) is not datetime
            or self.due_at.utcoffset() != timedelta(0)
            or type(self.safely_cancellable) is not bool
            or type(self.held) is not bool
            or any(
                type(value) is not str or not value or len(value) > 128
                for value in (self.target, self.slot)
            )
        ):
            raise ValueError("Recovery requires bounded canonical slot metadata.")


@dataclass(frozen=True)
class RecoveryPlan:
    """One group outcome; unchanged/held/terminal slots are deliberately omitted.

    Daily recovery requires a new aggregate occurrence, so ``selected`` is None
    and ``daily_dates`` names its exact source slots. The durable owner must bind
    every coalesced row to that aggregate and pin its inputs before dispatch.
    A blocked plan makes no mutations, even if some siblings are cancellable.
    """

    selected: UUID | None = None
    coalesced: tuple[UUID, ...] = ()
    skipped: tuple[UUID, ...] = ()
    reason: str = ""
    daily_dates: tuple[date, ...] = ()
    blocked: bool = False


def plan_recovery(
    slots,
    *,
    cutoff,
    closed=False,
    eligible=True,
    responded=False,
    deliverable=True,
    initial_delivered=False,
):
    """Choose at most one due Family/weekly message or one daily recovery digest.

    Stable UUID tie-breaking makes equal-due reminders deterministic. Future,
    restore-held and terminal work cannot be consumed by an overdue group.
    Uncertain provider acceptance blocks the group before any safe cancellations.
    Family status affects only Family mail, never completed-day reporting.
    """
    if (
        type(slots) is not tuple
        or len(slots) > 1000
        or any(not isinstance(row, RecoverySlot) for row in slots)
        or type(cutoff) is not datetime
        or cutoff.utcoffset() != timedelta(0)
        or any(
            type(value) is not bool
            for value in (closed, eligible, responded, deliverable, initial_delivered)
        )
    ):
        raise ValueError("Recovery requires a complete bounded group and UTC cutoff.")
    if not slots:
        return RecoveryPlan()
    _validate_group(slots)
    overdue = sorted(
        (row for row in slots if row.due_at <= cutoff and not row.held),
        key=lambda row: (row.due_at, row.definition_id.int, row.occurrence_id.int),
    )
    if any(
        row.state == "delivery_unknown"
        or (row.state in {"pending", "running"} and not row.safely_cancellable)
        for row in overdue
    ):
        return RecoveryPlan(blocked=True, reason="delivery_unresolved")
    candidates = [row for row in overdue if row.state in {"pending", "running"}]
    if not candidates:
        return RecoveryPlan()
    kind = slots[0].kind
    if kind in {"initial", "reminder"}:
        reason = (
            "campaign_closed"
            if closed
            else "family_ineligible"
            if not eligible
            else "family_responded"
            if responded
            else "no_deliverable_recipient"
            if not deliverable
            else ""
        )
        if reason:
            return RecoveryPlan(
                skipped=tuple(row.occurrence_id for row in candidates), reason=reason
            )
        if not initial_delivered and any(
            row.kind == "initial" and row.state in {"failed", "skipped", "coalesced"}
            for row in overdue
        ):
            # A reminder cannot replace an unsuccessful initial invitation.
            # Only explicit retry/resolution or the deliverability-recovery
            # owner may supply a new initial attempt; never revive this row.
            return RecoveryPlan(blocked=True, reason="initial_unfulfilled")
        initials = [row for row in candidates if row.kind == "initial"]
        # Successful initial coverage is supplied independently of current
        # revision rows. It is not inferred from a coalesced or skipped outcome.
        if initial_delivered and initials:
            raise ValueError("Fulfilled initial slots must be excluded by their owner.")
        selected = initials[0] if initials else candidates[-1]
    elif kind == "daily_digest" and len(candidates) > 1:
        dates = tuple(date.fromisoformat(row.slot) for row in candidates)
        return RecoveryPlan(
            coalesced=tuple(row.occurrence_id for row in candidates),
            reason="missed_daily_recovery",
            daily_dates=dates,
        )
    else:
        selected = candidates[-1]
    return RecoveryPlan(
        selected=selected.occurrence_id,
        coalesced=tuple(row.occurrence_id for row in candidates if row is not selected),
        reason=(
            "missed_family_recovery"
            if kind in {"initial", "reminder"}
            else "missed_weekly_recovery"
        )
        if len(candidates) > 1
        else "",
    )


def _validate_group(slots):
    """Reject cross-target/mode/type mixtures and duplicate semantic obligations."""
    first = slots[0]
    kinds = (
        {"initial", "reminder"}
        if first.kind in {"initial", "reminder"}
        else {first.kind}
    )
    if (
        any(
            row.mode != first.mode
            or row.target != first.target
            or row.kind not in kinds
            for row in slots
        )
        or len({row.occurrence_id for row in slots}) != len(slots)
        or len({(row.definition_id, row.slot) for row in slots}) != len(slots)
        or (
            first.kind not in {"initial", "reminder"}
            and len({row.definition_id for row in slots}) != 1
        )
        or sum(row.kind == "initial" for row in slots) > 1
    ):
        raise ValueError("Recovery slots must form one distinct semantic group.")
    if first.kind == "daily_digest":
        for row in slots:
            if date.fromisoformat(row.slot).isoformat() != row.slot:
                raise ValueError("Daily recovery needs canonical local-date slots.")
