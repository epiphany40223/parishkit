"""The roster Entered in ParishSoft tick on resolved Ministry requests (#528).

The ParishSoft API cannot change Ministry rosters, so a join or leave that a
person resolved (*joined* or *leave confirmed*) is entered in ParishSoft by
hand. Staff and Admin record that, or clear a mistaken mark, here: each set
or clear is one immutable ``MinistryRosterEntry`` row, and the latest row is
the current state. A request resolved because the ParishSoft roster already
showed the change (it has a resolution source) needs no tick. The request
itself is a closed outcome and never changes. SQL checks the actor, the
request, the sequence and the audit again
(``schema/migrations/0033_roster_entries.sql``).

Ministry leaders see the tick but never set it: its readers here take no
principal, and the views show it read-only to them.
"""

from uuid import UUID

from django.db.models import OuterRef, Subquery

from parishkit.stewardship.accounts.policy import Capability, allows, current_principal
from parishkit.stewardship.accounts.policy_models import PortalUser
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.reports.export_services import admit_campaign
from parishkit.stewardship.storage import StaleRecordError

from .models import MinistryRequest, MinistryRosterEntry

ROSTER_OUTCOMES = frozenset({"joined", "leave_confirmed"})


def eligible(request):
    """Whether a request takes the tick: a person resolved it as a roster change.

    ``request`` is a ``MinistryRequest`` or a follow-up row (a mapping with
    ``state``, ``outcome`` and ``source_resolved``).
    """
    if isinstance(request, MinistryRequest):
        return (
            request.state == "resolved"
            and request.outcome in ROSTER_OUTCOMES
            and request.resolution_source_id is None
        )
    return (
        request["state"] == "resolved"
        and request["outcome"] in ROSTER_OUTCOMES
        and not request["source_resolved"]
    )


def can_tick(principal):
    """Admin and Staff set or clear the tick; a Ministry leader never does.

    ``MINISTRY_FOLLOWUP`` without a Ministry is granted only to the global
    operational roles; a leader holds it only for their own Ministries.
    """
    return allows(principal, Capability.MINISTRY_FOLLOWUP)


def roster_marks(request_ids):
    """Each request's current tick: ``{id: {entered, sequence, at, by}}``.

    Requests with no row are absent (not entered, sequence 0). One query,
    for the requests a page shows.
    """
    latest = MinistryRosterEntry.objects.filter(request_id=OuterRef("request_id"))
    rows = (
        MinistryRosterEntry.objects.filter(request_id__in=list(request_ids))
        .filter(sequence=Subquery(latest.order_by("-sequence").values("sequence")[:1]))
        .values("request_id", "entered", "sequence", "created_at", "actor_id")
    )
    rows = list(rows)
    emails = dict(
        PortalUser.objects.filter(pk__in={row["actor_id"] for row in rows}).values_list(
            "pk", "email"
        )
    )
    return {
        str(row["request_id"]): {
            "entered": row["entered"],
            "sequence": row["sequence"],
            "at": row["created_at"],
            "by": emails.get(row["actor_id"], ""),
        }
        for row in rows
    }


def mark(row, marks):
    """Add the tick fields a page draws to one follow-up row, in place."""
    current = marks.get(str(row["id"]), {})
    row["roster_eligible"] = eligible(row)
    row["roster_entered"] = bool(current.get("entered"))
    row["roster_sequence"] = current.get("sequence", 0)
    row["roster_at"] = current.get("at")
    row["roster_by"] = current.get("by", "")
    return row


def set_roster_entered(store, actor_id, request_id, *, sequence, entered, request_key):
    """Set or clear one request's tick, replay-safe, under the work order.

    ``sequence`` is the tick's next sequence as the page saw it (its current
    sequence plus one), so a page drawn before someone else's change is
    refused as stale rather than undoing it. The same ``request_key`` with the
    same intent returns the first row; with another intent it is refused.
    """
    if (
        any(
            not isinstance(value, UUID) for value in (actor_id, request_id, request_key)
        )
        or type(sequence) is not int
        or not 1 <= sequence < 2**63 - 1
        or type(entered) is not bool
    ):
        raise ValueError("Invalid roster entry.")
    intent = dict(request_id=request_id, sequence=sequence, entered=entered)
    with work_transaction():
        if not can_tick(current_principal(store, actor_id)):
            raise PermissionError("The roster tick is unavailable.")
        target = (
            MinistryRequest.objects.select_for_update(of=("self",), no_key=True)
            .select_related("submission")
            .get(pk=request_id, submission__mode="live")
        )
        admit_campaign(target.submission.campaign_id, mutating=True)
        previous = MinistryRosterEntry.objects.filter(
            actor_id=actor_id, request_key=request_key
        ).first()
        if previous is not None:
            if any(getattr(previous, key) != value for key, value in intent.items()):
                raise ValueError("This request key is already bound.")
            return previous
        if not eligible(target):
            raise ValueError("This request does not take the roster tick.")
        current = (
            MinistryRosterEntry.objects.filter(request_id=request_id)
            .order_by("-sequence")
            .first()
        )
        if sequence != (current.sequence if current else 0) + 1:
            raise StaleRecordError("The roster tick changed.")
        if entered == (current.entered if current else False):
            raise ValueError("The roster tick already says so.")
        entry = MinistryRosterEntry.objects.create(
            actor_id=actor_id, request_key=request_key, **intent
        )
        system = SystemConfiguration.objects.select_related(
            "active_configuration__parish"
        ).get()
        record_action(
            Action.MINISTRY_ROSTER_ENTERED,
            actor_kind=ActorKind.PORTAL_USER,
            actor_id=actor_id,
            subject_id=entry.pk,
            parish_id=system.active_configuration.parish.pk,
            campaign_id=target.submission.campaign_id,
            context={
                "outcome": Outcome.CHANGED,
                "before_version": sequence - 1,
                "after_version": sequence,
                "ministry_duid": target.ministry_duid,
            },
        )
        return entry
