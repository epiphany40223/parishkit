"""``followup roster-entered`` and ``followup roster-bulk`` (ADM-11 PR 11b).

A join or leave a person resolved (*joined* or *leave confirmed*) is entered
in ParishSoft by hand, as the ParishSoft API cannot change Ministry rosters;
its Entered in ParishSoft tick records that (#528). The commands use the
pages' own pieces, so the two cannot drift:

- ``followup roster-entered`` is a request's tick (``admin:
  ministry_followup_roster``): the request page's admission
  (``ministry_followup_views._principal``) and its service
  (``workflows.roster.set_roster_entered``), which records
  ``ministry_roster_entered``.
- ``followup roster-bulk`` is the roster list's bulk Entered by hand
  (#879; on the page, Ministry requests' To enter by hand Stage since
  Roster changes to enter was folded into it, R39 finding 2): the list's
  admission (``ministry_roster_views._principal``, Administrators and
  Staff) and its service (``mark_many_entered``), all or none.

Each runs in the page's command scope (the command session's row locked,
the admission repeated around the effect) and records the command's
``admin_cmd_followup_roster_entered`` or ``admin_cmd_followup_roster_bulk``
in the same transaction, once per run that wrote an entry. The documents
print ids, sequences and instants only, never the Member. The host-only
command line adds no rate limit or hold beyond the page's.
"""

from dataclasses import dataclass
from uuid import UUID

from django.db import DatabaseError

from .admin_followup import InvalidFollowup, _admit_write, _record_command, problem
from .admin_reads import NotAvailable, ReadModel, _held, _recheck

# What the service's refusals of a tick mean, by its message, as the option
# that would correct them. The service raises plain ValueErrors (the page
# answers each with its 400), so the command names the option instead.
NOT_ELIGIBLE = (
    "This request does not take the Entered in ParishSoft tick: only a join "
    "or leave a person resolved as joined or leave confirmed does."
)
ALREADY = "The tick already says so."


def admit_with(principal, caller, store, *, final=False):
    """A page's own admission ``principal`` for a command's post, or refuse.

    Recording activity as the page's post does; ``final`` is the read-only
    recheck the command scope repeats, which writes nothing. A session that
    ended is exit 5 (``session_ended``), not the page's refusal.
    """
    from .accounts.automation_sessions import SessionUnusable
    from .accounts.sessions import authenticated_admin

    try:
        return principal(caller, store, read_only=final)
    except PermissionError:
        if authenticated_admin(caller, store=store, read_only=True) is None:
            raise SessionUnusable("session_ended") from None
        raise


def _admit_roster(caller, store, *, final=False):
    """Roster changes to enter's admission: Administrators and Staff only."""
    from .reports.ministry_roster_views import _principal

    return admit_with(_principal, caller, store, final=final)


def admit_roster(caller, service):
    """The list's admission, passive: ``followup roster-bulk``'s role-first check.

    Administrators and Staff tick; a Ministry leader never does, so a
    leader is ``denied`` before any input or prompt. Records no activity.
    """
    return _admit_roster(caller, service.store, final=True)


def _tick_admission(caller, store, *, final=False):
    """The request page's admission, then the tick's own role rule.

    The page admits any reader of the follow-up queue (a Ministry leader
    too) and its service then refuses a leader (``can_tick``); the command
    refuses the leader at once, so a request outside the leader's reach is
    ``denied`` like any other, never ``not_available``.
    """
    from .workflows.roster import can_tick

    actor = _admit_write(caller, store, final=final)
    if not can_tick(actor):
        raise PermissionError("The roster tick is unavailable.")
    return actor


def _in_current(campaign_id, identities):
    """Refuse a request that is not a Live request of the current campaign.

    The page binds its route's campaign (``_in_campaign``); the command
    binds the current one, and says ``not_available``, as ``followup show``
    answers such a request.
    """
    from .workflows.models import MinistryRequest

    found = MinistryRequest.objects.filter(
        pk__in=list(identities),
        submission__campaign_id=campaign_id,
        submission__mode="live",
    ).count()
    if found != len(identities):
        raise NotAvailable("No such Ministry request.")


def _refused(error, *, field):
    """The service's ValueError as an ``InvalidFollowup`` at ``field``.

    A request key already bound to another tick stays a plain ``invalid``,
    as for every keyed command.
    """
    message = str(error)
    if message.startswith("This request does not take"):
        return InvalidFollowup([problem("request", NOT_ELIGIBLE)])
    if message.startswith("The roster tick already says so"):
        return InvalidFollowup([problem(field, ALREADY)])
    return error


def _entry(entry):
    """One tick entry as the documents print it: ids, sequence and instant."""
    return {
        "id": entry.pk,
        "request_id": entry.request_id,
        "sequence": entry.sequence,
        "entered": entry.entered,
        "created_at": entry.created_at,
    }


def scoped_write(caller, service, admit, actor, context, step):
    """Run ``step(actor, written)`` in the page's command scope.

    ``written`` records whether the command's event was added, so a
    database error after it is ``outcome_unknown``; ``context["committed"]``
    is set once the write committed. A refusal is rechecked against the
    session first, so a session that ended meanwhile is exit 5.
    """
    from .jobs.task_retries import command_scope
    from .observability import _guard_refusal

    written = []

    def scoped():
        """The step in the command scope, starting over after a hold."""
        written.clear()
        with command_scope(caller, service, actor, admit=admit):
            return step(actor, written)

    try:
        model = _held(scoped)
    except DatabaseError as error:
        if written and not _guard_refusal(error):
            context["committed"] = True
        raise
    except (NotAvailable, PermissionError) as error:
        _recheck(caller, service.store, actor)
        raise error
    context["committed"] = True
    return model


@dataclass(frozen=True)
class RosterEntered(ReadModel):
    """The tick entry a ``followup roster-entered`` wrote, or found for its key.

    ``entry`` is the immutable record (``id``, ``request_id``, ``sequence``,
    ``entered``, ``created_at``); ``created`` is false for a repeat.
    """

    created: bool
    request_key: UUID
    entry: dict


def roster_entered(
    caller, service, request_id, *, expected_version, entered, request_key, context
):
    """``followup roster-entered``: set or clear one request's tick.

    ``expected_version`` is the tick's current sequence (``followup show``'s
    ``roster.sequence``, 0 before any tick); the page's form posts the next
    one, so the service gets ``expected_version + 1`` and refuses a tick
    someone changed since as stale. The same key with the same change
    returns its first entry and records nothing; with another change it is
    ``invalid``. A new entry records the service's ``ministry_roster_entered``
    and the command's ``admin_cmd_followup_roster_entered``.
    """
    from .admin_report_reads import _current
    from .audit.schemas import Action
    from .campaigns.work_locks import require_work_order
    from .workflows.models import MinistryRosterEntry
    from .workflows.roster import set_roster_entered

    actor = _tick_admission(caller, service.store)
    context["request_id"] = str(request_key)

    def step(actor, written):
        """The page's tick, with the command's event for a new entry."""
        _in_current(_current(), [request_id])
        # The scope's work order serializes repeats of one key (#1222).
        require_work_order()
        repeat = MinistryRosterEntry.objects.filter(
            actor_id=actor.identity, request_key=request_key
        ).exists()
        try:
            entry = set_roster_entered(
                service.store,
                actor.identity,
                request_id,
                sequence=expected_version + 1,
                entered=entered,
                request_key=request_key,
            )
        except ValueError as error:
            raise _refused(error, field="set" if entered else "clear") from None
        if not repeat:
            _record_command(
                Action.ADMIN_CMD_FOLLOWUP_ROSTER_ENTERED, actor, caller, written
            )
        return RosterEntered(
            created=not repeat, request_key=request_key, entry=_entry(entry)
        )

    return scoped_write(caller, service, _tick_admission, actor, context, step)


def check_bulk(versions):
    """Refuse a choice longer than the list's bulk bar takes (one page, 250).

    ``followup roster-bulk`` calls this before its prompt, so nothing is
    asked about a choice that cannot be recorded.
    """
    from .workflows.roster import MAX_BULK

    if len(versions) > MAX_BULK:
        raise InvalidFollowup(
            [problem("request", f"Choose at most {MAX_BULK} requests at once.")]
        )


@dataclass(frozen=True)
class RosterBulk(ReadModel):
    """The entries a ``followup roster-bulk`` wrote, or found for its key.

    ``changed`` counts the entries this run wrote (0 for a repeat) and
    ``entries`` lists every chosen request's entry, in request id order.
    """

    created: bool
    request_key: UUID
    changed: int
    entries: list


def roster_bulk(caller, service, versions, *, request_key, context):
    """``followup roster-bulk``: mark several requests entered, all or none.

    ``versions`` maps each chosen request to its tick's current sequence;
    the list's bulk bar posts the next one, as here. Each entry's key comes
    from ``request_key`` and its request (the service's ``uuid5``), so a
    repeat replays every entry, and the same key with more requests writes
    only the new ones. A tick that changed since is ``stale_version``; a
    request that takes no tick, or is already marked, is ``invalid``. A run
    that wrote an entry records ``admin_cmd_followup_roster_bulk`` once.
    """
    from uuid import uuid5

    from .admin_report_reads import _current
    from .audit.schemas import Action
    from .campaigns.work_locks import require_work_order
    from .workflows.models import MinistryRosterEntry
    from .workflows.roster import mark_many_entered

    check_bulk(versions)
    actor = _admit_roster(caller, service.store)
    context["request_id"] = str(request_key)
    keys = [uuid5(request_key, str(identity)) for identity in versions]

    def step(actor, written):
        """The list's bulk mark, with the command's event when it wrote."""
        _in_current(_current(), list(versions))
        require_work_order()
        replayed = MinistryRosterEntry.objects.filter(
            actor_id=actor.identity, request_key__in=keys
        ).count()
        try:
            entries = mark_many_entered(
                service.store,
                actor.identity,
                {identity: version + 1 for identity, version in versions.items()},
                bulk_key=request_key,
            )
        except ValueError as error:
            raise _refused(error, field="request") from None
        changed = len(entries) - replayed
        if changed:
            _record_command(
                Action.ADMIN_CMD_FOLLOWUP_ROSTER_BULK, actor, caller, written
            )
        return RosterBulk(
            created=bool(changed),
            request_key=request_key,
            changed=changed,
            entries=[_entry(entry) for entry in entries],
        )

    return scoped_write(caller, service, _admit_roster, actor, context, step)
