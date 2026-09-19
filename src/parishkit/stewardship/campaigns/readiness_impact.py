"""Count prospective Family mail using the execution owner's coalescing policy.

This is a read-only projection, not permission to dispatch. The database reader
must supply complete, current Production groups (never Testing fulfillment),
in Family UUID order, and bind its result to the source/configuration versions
and cutoff. Streaming bounds memory independently of the parish population.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from .schedule_recovery import RecoveryPlan, RecoverySlot, plan_recovery


@dataclass(frozen=True)
class FamilyImpactInput:
    """One complete Family group; no name, address or code is needed for counts."""

    family_id: UUID
    active: bool
    email_eligible: bool
    deliverable: bool
    responded: bool
    initial_delivered: bool
    slots: tuple[RecoverySlot, ...]
    initial_unreviewed: bool = False

    def __post_init__(self):
        """Prevent a partial/cross-Family or Testing group from inflating readiness."""
        if (
            not isinstance(self.family_id, UUID)
            or any(
                type(value) is not bool
                for value in (
                    self.active,
                    self.email_eligible,
                    self.deliverable,
                    self.responded,
                    self.initial_delivered,
                    self.initial_unreviewed,
                )
            )
            or type(self.slots) is not tuple
            or len(self.slots) > 100
            or any(
                not isinstance(row, RecoverySlot)
                or row.mode != "production"
                or row.target != f"family:{self.family_id}"
                or row.kind not in {"initial", "reminder"}
                for row in self.slots
            )
        ):
            raise ValueError("Readiness needs a complete Production Family group.")


@dataclass(frozen=True)
class FamilyImpact:
    """Messages and coalesced semantic slots are deliberately separate units.

    No-email counts describe eligibility, not transient provider deliverability.
    Held groups remain visible instead of looking like a verified empty queue.
    Testing responses must not appear as Production response suppression.
    """

    families: int = 0
    active: int = 0
    email_eligible: int = 0
    no_eligible_email: int = 0
    deliverable: int = 0
    messages: int = 0
    coalesced_slots: int = 0
    skipped_slots: int = 0
    blocked_families: int = 0


def summarize_family_impact(groups, *, cutoff, closed=False):
    """Exhaust a sorted stream; never label a truncated first page as the total.

    The shared planner accounts for initial invitation precedence, successful
    live coverage, uncertain delivery, and current eligibility. A repeated or
    out-of-order Family rejects the whole calculation; partial totals are never
    returned after an iterator/read failure.
    """
    if (
        type(cutoff) is not datetime
        or cutoff.utcoffset() != timedelta(0)
        or type(closed) is not bool
    ):
        raise ValueError("Readiness impact requires a UTC cutoff and close state.")
    totals = dict.fromkeys(FamilyImpact.__dataclass_fields__, 0)
    previous = -1
    for group in groups:
        if not isinstance(group, FamilyImpactInput) or group.family_id.int <= previous:
            raise ValueError("Readiness Families must be distinct and UUID-ordered.")
        previous = group.family_id.int
        decision = plan_recovery(
            group.slots,
            cutoff=cutoff,
            closed=closed,
            eligible=group.active and group.email_eligible,
            responded=group.responded,
            deliverable=group.deliverable,
            initial_delivered=group.initial_delivered,
        )
        if group.initial_unreviewed and any(
            row.occurrence_id == decision.selected and row.kind == "reminder"
            for row in group.slots
        ):
            # Match dispatch: an unresolved initial blocks a selected reminder,
            # not ineligible/responded/closed skips or unrelated held reminders.
            decision = RecoveryPlan(blocked=True, reason="restore_delivery_unresolved")
        totals["families"] += 1
        totals["active"] += group.active
        totals["email_eligible"] += group.active and group.email_eligible
        totals["no_eligible_email"] += group.active and not group.email_eligible
        totals["deliverable"] += (
            group.active and group.email_eligible and group.deliverable
        )
        totals["messages"] += decision.selected is not None
        totals["coalesced_slots"] += len(decision.coalesced)
        totals["skipped_slots"] += len(decision.skipped)
        totals["blocked_families"] += decision.blocked
    return FamilyImpact(**totals)
