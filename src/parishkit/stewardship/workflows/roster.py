"""The roster Entered in ParishSoft tick on resolved Ministry requests (#528).

The ParishSoft API cannot change Ministry rosters, so a join or leave that a
person resolved (*joined* or *leave confirmed*) is entered in ParishSoft by
hand. Staff and Admin record that here: each set or clear is one immutable
``MinistryRosterEntry`` row, and the latest row is the current state.
Clearing a mark is Back to review of Entered by hand, with a required
note (#1840): ``review.undo_entered`` and the worklist's undo record it,
as a revision paired with the clearing row (migration 0212). Ministry
requests' bulk Entered by hand marks several at once
(``mark_many_entered``, #879), each exactly as a single tick is recorded.
A request resolved because the ParishSoft roster already showed the
change (it has a resolution source) needs no tick. SQL checks the actor,
the request, the sequence and the audit again
(``schema/migrations/0061_roster_entries.sql``,
``0212_ministry_terminal_undo.sql``).

Ministry leaders see the tick but never set it: its readers here take no
principal, and the views show it read-only to them.
"""

from uuid import UUID, uuid5

from django.db.models import OuterRef, Subquery

from parishkit.stewardship.accounts.policy import Capability, allows, current_principal
from parishkit.stewardship.accounts.policy_models import PortalUser
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.reports.export_services import admit_campaign
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.tables import PAGE_SIZES

from .models import MinistryRequest, MinistryRosterEntry

ROSTER_OUTCOMES = frozenset({"joined", "leave_confirmed"})
# The bulk bar acts on the ticked rows of one page at most.
MAX_BULK = max(PAGE_SIZES)


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
    """Admin and Staff set the tick or undo it; a Ministry leader never does.

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
            # A marker whose portal user is gone, named as the other
            # worklists name one.
            "by": emails.get(row["actor_id"], "Former portal user"),
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


def _valid_keys(actor_id, request_key):
    """Whether the actor and request key are UUIDs, as every tick needs."""
    return isinstance(actor_id, UUID) and isinstance(request_key, UUID)


def _valid_sequence(sequence):
    """Whether ``sequence`` is a next sequence a tick can take."""
    return type(sequence) is int and 1 <= sequence < 2**63 - 1


def _record(actor_id, target, *, sequence, entered, request_key, system):
    """Write one tick on a locked, admitted request, with its audit event.

    The caller holds the work order, the request's row lock and the
    campaign's admission. A replay (the same actor and ``request_key``)
    returns the first row, or is refused when it meant something else. A
    request that does not take the tick, or a tick that already says so, is
    a ValueError; a sequence other than the current one plus one is stale.
    """
    intent = dict(request_id=target.pk, sequence=sequence, entered=entered)
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
        MinistryRosterEntry.objects.filter(request_id=target.pk)
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


def _system():
    """The active parish's system row, which every audit event names."""
    return SystemConfiguration.objects.select_related(
        "active_configuration__parish"
    ).get()


def set_roster_entered(store, actor_id, request_id, *, sequence, entered, request_key):
    """Set one request's tick, replay-safe, under the work order.

    ``sequence`` is the tick's next sequence as the page saw it (its current
    sequence plus one), so a page drawn before someone else's change is
    refused as stale rather than undoing it. The same ``request_key`` with the
    same intent returns the first row; with another intent it is refused.
    Clearing it is Back to review, with a note (``review.undo_entered``,
    #1840), so ``entered`` false is refused here.
    """
    if (
        not _valid_keys(actor_id, request_key)
        or not isinstance(request_id, UUID)
        or not _valid_sequence(sequence)
        or entered is not True
    ):
        raise ValueError("Invalid roster entry.")
    with work_transaction():
        if not can_tick(current_principal(store, actor_id)):
            raise PermissionError("The roster tick is unavailable.")
        target = (
            MinistryRequest.objects.select_for_update(of=("self",), no_key=True)
            .select_related("submission")
            .get(pk=request_id, submission__mode="live")
        )
        admit_campaign(target.submission.campaign_id, mutating=True)
        return _record(
            actor_id,
            target,
            sequence=sequence,
            entered=entered,
            request_key=request_key,
            system=_system(),
        )


class RosterStale(StaleRecordError):
    """One chosen tick changed after the list was shown; names its request."""

    def __init__(self, request_id):
        super().__init__("The roster tick changed.")
        self.request_id = request_id


def mark_many_entered(store, actor_id, sequences, *, bulk_key):
    """Mark many requests Entered in ParishSoft at once, all or none.

    A bulk Entered by hand (#879): ``sequences`` maps each
    chosen request id to its tick's next sequence as the list showed it.
    Each request gets its own entry and audit event, exactly as a single
    tick does (``_record``), in one transaction under the shared work order,
    with every request locked in primary-key order first, so two people
    marking overlapping rows cannot deadlock. Each entry's request key comes
    from ``bulk_key`` and its request (``uuid5``), so posting the same
    choice again replays every entry instead of writing a second one. A
    request that changed since the list was shown raises ``RosterStale``
    naming it; one that does not take the tick, or is already marked, is a
    ValueError. Nothing is saved then. Returns the entries, in id order.
    """
    if (
        not _valid_keys(actor_id, bulk_key)
        or not isinstance(sequences, dict)
        or not 1 <= len(sequences) <= MAX_BULK
        or any(
            not isinstance(key, UUID) or not _valid_sequence(value)
            for key, value in sequences.items()
        )
    ):
        raise ValueError("Invalid roster entries.")
    with work_transaction():
        if not can_tick(current_principal(store, actor_id)):
            raise PermissionError("The roster tick is unavailable.")
        targets = list(
            MinistryRequest.objects.select_for_update(of=("self",), no_key=True)
            .select_related("submission")
            .filter(pk__in=sequences, submission__mode="live")
            .order_by("pk")
        )
        if len(targets) != len(sequences):
            raise PermissionError("A Ministry request is unavailable.")
        for campaign in {target.submission.campaign_id for target in targets}:
            admit_campaign(campaign, mutating=True)
        system = _system()
        entries = []
        for target in targets:
            try:
                entries.append(
                    _record(
                        actor_id,
                        target,
                        sequence=sequences[target.pk],
                        entered=True,
                        request_key=uuid5(bulk_key, str(target.pk)),
                        system=system,
                    )
                )
            except StaleRecordError:
                raise RosterStale(target.pk) from None
    return entries
