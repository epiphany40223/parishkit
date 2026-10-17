"""``followup …``: the Ministry follow-up queue from the command line (ADM-11).

The follow-up queue is part of Ministry requests (``admin:ministry_report``,
``reports.ministry_followup_views``): the join and leave requests Families
made, which Staff, Administrators and Ministry leaders follow up (#1133).
The commands use the page's own pieces, so the two cannot drift:

- ``followup list`` is the queue's table and ``followup show`` one request's
  page, read through the page's admission (``_principal``), the shared
  report read (``admin_report_reads._report``: the current campaign,
  ``admit_report_read`` and the campaign read guard), the page's selection
  (``followup_page``, with its filters and scope) and its history
  (``followup_history``). Both record the page's ``ministry_followup_viewed``
  (started, then succeeded or failed).
- ``followup update`` is a request page's Save, through the page's own
  service (``workflows.followup.update_request``), and ``followup bulk`` the
  queue's bulk outcome (#1131, ``apply_bulk_outcome``). Each runs in the
  page's command scope (the command session's row locked, the admission
  repeated around the effect) and records the services' own events plus the
  command's ``admin_cmd_followup_update`` or ``admin_cmd_followup_bulk``,
  for each new revision (the key binds each request, as on the page).

Every row names a Member of a Family, so the documents print ids, versions,
states, outcomes, Ministries (DUID and name) and instants only: never the
Member's name or DUID, the notes, or a contact attempt's notes. The Member's
standing in ParishSoft is printed as its stored status. The host-only
command line adds no rate limit or hold beyond the page's.
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from django.db import DatabaseError

from .admin_reads import NotAvailable, ReadModel, _held, _recheck

# The page's queue filters the command line takes (``FollowupQuery``), never
# its search, which names Members and would stay in shell history.
FILTERS = ("ministry", "action", "state", "outcome", "sort", "page", "size")


def _row(row):
    """One selection row as the documents print it: no Member, no notes.

    ``ministry`` is the Ministry's DUID. ``member_status`` is the Member's
    standing in ParishSoft (``current``, or why the queue marks them, #1217),
    and ``roster`` the Entered in ParishSoft tick of a resolved join or
    leave (#528): ``eligible``, ``entered`` and its current ``sequence``.
    """
    return {
        "id": row["id"],
        "version": row["version"],
        "ministry": row["ministry_duid"],
        "ministry_name": row["ministry_name"],
        "in_campaign": row["in_campaign"],
        "kind": row["entity_kind"],
        "action": row["action"],
        "state": row["state"],
        "outcome": row["outcome"],
        "open": row["open"],
        "member_status": row.get("member_status") or "current",
        "submitted_at": row["submitted_at"],
        "resolved_at": row["resolved_at"],
        "source_resolved": row["source_resolved"],
        "latest": row["latest"],
        "last_contact_at": row["last_contact_at"],
        "roster": {
            "eligible": row["roster_eligible"],
            "entered": row["roster_entered"],
            "sequence": row["roster_sequence"],
        },
    }


def _marked(rows):
    """Add the roster tick to the rows shown, as the page does (#528)."""
    from .workflows.roster import mark, roster_marks

    marks = roster_marks(row["id"] for row in rows)
    for row in rows:
        mark(row, marks)
    return rows


@dataclass(frozen=True)
class FollowupList(ReadModel):
    """One page of the follow-up queue of the current campaign.

    ``filters`` are the page's choices (``ministry``, ``action``, ``state``,
    ``outcome``, ``sort``), never its search; ``page`` and ``size`` the
    window, moved back to the last page when it asked for one past it, as
    the page does. ``total`` counts every request the filters keep, and
    ``rows`` holds this page's, each as ``_row`` prints it.
    """

    campaign_id: UUID
    filters: dict
    page: int
    size: int
    total: int
    rows: list


@dataclass(frozen=True)
class FollowupShow(ReadModel):
    """One request's page: the request and a page of its follow-up history.

    ``request`` is the row ``followup list`` prints. ``history`` is the
    page's history, newest first, across the Family's resubmissions of the
    same request (``request_id`` names the request each revision was
    recorded on): ``id``, ``expected_version``, ``state``, ``outcome``,
    ``contact_channel``, ``contact_at``, ``actor_id``, ``created_at`` and
    ``bulk`` (recorded by a bulk outcome). Never the notes or a contact
    attempt's notes. ``more_history`` says whether another page follows.
    """

    campaign_id: UUID
    request: dict
    history_page: int
    history: list
    more_history: bool


def _principal():
    """The page's own admission, imported once Django is set up."""
    from .reports.ministry_followup_views import _principal as admission

    return admission


def _audit(actor, campaign_id, outcome, ministry):
    """The page's view event, with the request's Ministry on a request page."""
    from .reports.ministry_followup_views import _audit as audit

    audit(actor, campaign_id, outcome, ministry)


def _parsed(caller, service, actor, values):
    """The page's filter check, before anything is recorded.

    A refusal (``invalid``) is reported after the session recheck, so an
    ended session is exit 5 whatever the filters.
    """
    from .reports.ministry_followup import FollowupQuery

    try:
        return FollowupQuery.parse(values)
    except ValueError:
        _recheck(caller, service.store, actor)
        raise


def read_followup_list(caller, service, filters):
    """``followup list``: one page of the queue, as the queue's table shows it.

    ``filters`` holds the page's non-identifying choices by name (None when
    not given). Admission, the campaign check and the guarded read are the
    shared report read's, so no current campaign is ``not_available`` and
    the campaign's follow-up being off is ``denied``. Records the page's
    ``ministry_followup_viewed``, started and then succeeded or failed.
    """
    from .admin_report_reads import _report
    from .reports.ministries import MinistryQuery, ministry_page
    from .reports.ministry_followup import followup_page
    from .reports.report_paging import clamp_query

    given = {key: str(value) for key, value in filters.items() if value is not None}
    holder = {}

    def admit(actor, campaign_id):
        """The page's filter check."""
        holder["query"] = _parsed(caller, service, actor, given)

    def read(guard, actor, campaign_id):
        """The page's selection, its last page if asked past it, and its ticks."""
        query = holder["query"]
        result = followup_page(campaign_id, query, actor, empty=True)
        moved = clamp_query(query, result["total"], query.page_size)
        if moved is not None:
            query = moved
            result = followup_page(campaign_id, query, actor, empty=True)
        # The queue page also reads the Ministry report's selection, whose
        # cross-check refuses a reader SQL does not treat as operational
        # (``ministry_followup._admit``); so does the command.
        ministry_page(campaign_id, MinistryQuery(), actor, every=True)
        rows = _marked(result["rows"])
        model = FollowupList(
            campaign_id=campaign_id,
            filters={
                key: value
                for key, value in query.form_values().items()
                if key not in {"search", "history"}
            },
            page=query.page,
            size=query.page_size,
            total=result["total"],
            rows=[_row(row) for row in rows],
        )
        return model, None

    def audit(actor, campaign_id, outcome, values):
        """The queue page's view event."""
        _audit(actor, campaign_id, outcome, None)

    return _report(
        caller,
        service,
        principal=_principal(),
        read=read,
        audit=audit,
        started=True,
        admit=admit,
    )


def _history_row(revision):
    """One history revision as ``followup show`` prints it: no notes."""
    return {
        "id": revision.pk,
        "request_id": revision.request_id,
        "expected_version": revision.expected_version,
        "state": revision.state,
        "outcome": revision.outcome,
        "contact_channel": revision.contact_channel,
        "contact_at": revision.contact_at,
        "actor_id": revision.actor_id,
        "created_at": revision.created_at,
        "bulk": revision.bulk,
    }


def read_followup_show(caller, service, request_id, *, history_page=1):
    """``followup show``: one request's page, without its Member or notes.

    A request that is not one of the current campaign's, or outside the
    reader's scope, is ``not_available``, as the page shows no such
    request. ``history_page`` is the page's history page (from 1). Records
    the page's ``ministry_followup_viewed``, naming the request's Ministry
    once it was read, as the page does.
    """
    from .admin_report_reads import _report
    from .reports.ministry_followup import (
        FollowupQuery,
        followup_history,
        followup_page,
    )

    if type(history_page) is not int or history_page < 1:
        raise ValueError("A history page is a positive number.")

    def read(guard, actor, campaign_id):
        """The page's one-request selection and its history."""
        result = followup_page(
            campaign_id,
            FollowupQuery(state="any", history="all"),
            actor,
            request_id=request_id,
        )
        [row] = _marked(result["rows"])
        history, more = followup_history(request_id, history_page)
        model = FollowupShow(
            campaign_id=campaign_id,
            request=_row(row),
            history_page=history_page,
            history=[_history_row(revision) for revision in history],
            more_history=more,
        )
        return model, row["ministry_duid"]

    def audit(actor, campaign_id, outcome, ministry):
        """The request page's view event."""
        _audit(actor, campaign_id, outcome, ministry)

    return _report(
        caller,
        service,
        principal=_principal(),
        read=read,
        audit=audit,
        started=True,
    )


# ------------------------------------------------------------------ changes


def _admit_write(caller, store, *, final=False):
    """The page's admission of a follow-up post, or refuse.

    The page's own ``_principal``, recording activity as its post does.
    ``command_scope`` repeats it (``final``) inside the transaction as a
    read-only recheck that writes nothing. A session that ended is exit 5
    (``session_ended``), not the page's refusal.
    """
    from .accounts.automation_sessions import SessionUnusable
    from .accounts.sessions import authenticated_admin

    try:
        return _principal()(caller, store, read_only=final)
    except PermissionError:
        if authenticated_admin(caller, store=store, read_only=True) is None:
            raise SessionUnusable("session_ended") from None
        raise


def admit_followup(caller, service):
    """The page's own admission, passive: ``followup bulk``'s role-first check.

    The page admits Staff, Administrators and any Ministry leader who leads
    at least one Ministry (``can_follow_up``), so a leader may run a bulk
    outcome on their own Ministries' requests; each chosen request is then
    checked against the leader's Ministries (``_admitted``). Records no
    activity; an ended session is exit 5.
    """
    return _admit_write(caller, service.store, final=True)


class InvalidFollowup(ValueError):
    """A follow-up change the page refuses as correctable (``invalid``).

    ``fields`` lists each problem as ``{"field", "code", "message"}``, with
    the field named by its command-line option (``outcome``, ``notes``,
    ``contact_at``, ``contact_notes`` or ``request``) and a plain message.
    """

    def __init__(self, fields):
        """Keep the field problems the error document lists."""
        super().__init__("The follow-up change is not valid.")
        self.fields = fields


def problem(field, message):
    """One field problem of an ``InvalidFollowup``."""
    return {"field": field, "code": "invalid", "message": str(message)}


# The page's form fields, by the command-line option that sets them.
OPTION_FIELDS = {
    "outcome": "outcome",
    "notes": "notes",
    "contact_date": "contact_at",
    "contact_time": "contact_at",
}
# The page's future-contact message is about the browser's clock; on the
# command line the instant was typed, so it says so instead.
FUTURE_CONTACT = (
    "--contact-at is in the future; give when the contact attempt was made."
)


def refused(refusal):
    """The page's correctable refusal as an ``InvalidFollowup``.

    The message is the request page's (``_refusal_error``), except for a
    contact in the future; the fields are the options that set the page's
    fields it names, each once.
    """
    from .reports.ministry_followup_views import _refusal_error

    if refusal.code == "contact_future":
        return InvalidFollowup([problem("contact_at", FUTURE_CONTACT)])
    error = _refusal_error(refusal, {})
    names = dict.fromkeys(OPTION_FIELDS[name] for name in error["fields"])
    return InvalidFollowup([problem(name, error["message"]) for name in names])


def check_lengths(*, notes=None, contact_notes=None, requests=None):
    """Refuse input longer than the page takes, as field errors.

    The page's form bounds the notes (5,000 characters), the contact notes
    (2,000) and a bulk choice (one page of the queue, 250 requests); past
    them the service would refuse with no field named. ``followup bulk``
    calls this before its prompt, so nothing is asked about a choice that
    cannot be recorded.
    """
    from .workflows.followup import MAX_BULK, MAX_CONTACT_NOTES, MAX_NOTES

    problems = []
    if notes is not None and len(notes) > MAX_NOTES:
        problems.append(
            problem("notes", f"The notes are longer than {MAX_NOTES:,} characters.")
        )
    if contact_notes is not None and len(contact_notes) > MAX_CONTACT_NOTES:
        problems.append(
            problem(
                "contact_notes",
                f"The contact notes are longer than {MAX_CONTACT_NOTES:,} characters.",
            )
        )
    if requests is not None and len(requests) > MAX_BULK:
        problems.append(
            problem("request", f"Choose at most {MAX_BULK} requests at once.")
        )
    if problems:
        raise InvalidFollowup(problems)


def contact_instant(text):
    """``--contact-at``: an ISO 8601 instant with its UTC offset.

    The browser sends the contact's date, time and zone; the command line
    takes the instant itself, so no zone is guessed (#558). Anything else is
    ``invalid`` at ``contact_at``.
    """
    try:
        value = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        value = None
    if value is None or value.utcoffset() is None:
        raise InvalidFollowup(
            [
                problem(
                    "contact_at",
                    "Give the contact attempt's date and time as an ISO 8601 "
                    "instant with its offset, such as 2054-10-05T14:30:00-04:00.",
                )
            ]
        )
    return value


def _contact_change(*, state, outcome, notes, channel, contact_at, contact_notes):
    """A ``WorkflowChange`` with the optional contact attempt.

    Shared by a Save and the bulk outcome, as the page shares
    ``_contact_values``: a channel needs its instant (the page's
    ``contact_incomplete``), and the instant and notes count only with a
    channel. A malformed change is a ``ValueError`` (``invalid``); a
    correctable one is the page's refusal, at its option.
    """
    from .workflows.followup import FollowupRefusal, WorkflowChange

    try:
        if channel is not None and contact_at is None:
            raise FollowupRefusal("contact_incomplete")
        return WorkflowChange(
            state=state,
            outcome=outcome,
            notes=notes,
            contact_channel=channel,
            contact_at=contact_instant(contact_at) if channel else None,
            contact_notes=(contact_notes or "") if channel else "",
        )
    except FollowupRefusal as refusal:
        raise refused(refusal) from None


def change_values(*, state, outcome, notes, channel, contact_at, contact_notes):
    """The page's change from the command's options (``change_values``).

    As on the page, Closed: no response always records No response and only
    Resolved takes a chosen outcome; the command line refuses an outcome
    with any other state before it gets here (a usage error).
    """
    return _contact_change(
        state=state,
        outcome={"resolved": outcome, "closed_no_response": "no_response"}.get(state),
        notes=notes,
        channel=channel,
        contact_at=contact_at,
        contact_notes=contact_notes,
    )


def _admitted(actor, store, campaign_id, versions, replayed):
    """Check the chosen requests first, as the page's ``_refused`` does.

    Before any field rule, so a form mistake never hides a conflict and a
    request outside the reader's reach never gets a field error:

    - a request that is not a Live request of the current campaign, or is
      outside the actor's Ministries, is ``not_available``, the same answer
      for both, so nothing says such a request exists (as ``followup show``
      answers it);
    - a request changed since its version, or no longer open, is
      ``stale_version``, unless its revision for this key was already
      recorded (``replayed``): a repeat returns that revision instead.
    """
    from .accounts.policy import Capability, allows
    from .storage import StaleRecordError
    from .workflows.followup import authorize_ministry
    from .workflows.models import OPEN_STATES, MinistryRequest

    rows = list(
        MinistryRequest.objects.filter(
            pk__in=list(versions),
            submission__campaign_id=campaign_id,
            submission__mode="live",
        ).values_list("pk", "version", "state", "ministry_duid")
    )
    if len(rows) != len(versions):
        raise NotAvailable("No such Ministry request.")
    for _, _, _, ministry in rows:
        try:
            if not allows(actor, Capability.MINISTRY_FOLLOWUP, ministry_id=ministry):
                raise PermissionError("Outside this reader's Ministries.")
            authorize_ministry(store, actor.identity, ministry)
        except PermissionError:
            raise NotAvailable("No such Ministry request.") from None
    for pk, version, state, _ in rows:
        if pk not in replayed and (version != versions[pk] or state not in OPEN_STATES):
            raise StaleRecordError("The Ministry request changed; read it again.")


def _requests_now(identities):
    """The requests' state as the command's transaction leaves them, in id order."""
    from .workflows.models import MinistryRequest

    return list(
        MinistryRequest.objects.filter(pk__in=identities)
        .order_by("pk")
        .values("id", "version", "state", "outcome")
    )


def _followup_write(caller, service, versions, keys, *, request_key, context, write):
    """One follow-up post of the page, in the page's command scope.

    ``followup update`` and ``followup bulk`` share it. ``versions`` maps
    each chosen request to its expected version and ``keys`` to the replay
    key its revision is recorded under. Admits as the page's post does,
    then in the page's command scope (the session row locked, the admission
    repeated around the effect) finds the current campaign, reads which
    requests already have their revision for this key (``replayed``), and
    checks the requests first (``_admitted``). ``write(actor, written,
    replayed)`` is then the page's own service, which records the page's
    events; it returns the document, and records the command's event in the
    same transaction only when it changed something.

    A request whose Member ParishSoft no longer lists (#1217) is
    ``stale_version`` too. ``context["request_id"]`` is the key, so an
    exit-6 document names it; ``context["committed"]`` is set once the
    write may have committed.
    """
    from .admin_report_reads import _current
    from .campaigns.work_locks import require_work_order
    from .jobs.task_retries import command_scope
    from .observability import _guard_refusal
    from .workflows.models import MinistryWorkflowRevision

    actor = _admit_write(caller, service.store)
    context["request_id"] = str(request_key)
    written = []

    def step():
        """The page's write in its command scope, with the command's event."""
        # A step repeated after an activating change starts over: nothing
        # from an earlier attempt, which rolled back, counts as written.
        written.clear()
        with command_scope(caller, service, actor, admit=_admit_write):
            campaign_id = _current()
            # The work-order lock the scope holds serializes concurrent
            # repeats, so this tells a repeat from a new key; fail closed if
            # the scope ever stops holding it (#1222, #147).
            require_work_order()
            replayed = {
                request_id
                for request_id, key in MinistryWorkflowRevision.objects.filter(
                    actor_id=actor.identity, request_key__in=list(keys.values())
                ).values_list("request_id", "request_key")
                if keys.get(request_id) == key
            }
            _admitted(actor, service.store, campaign_id, versions, replayed)
            return write(actor, written, replayed)

    try:
        model = _held(step)
    except DatabaseError as error:
        if written and not _guard_refusal(error):
            context["committed"] = True
        raise
    except (NotAvailable, PermissionError) as error:
        # A session that ended meanwhile is exit 5, not the refusal.
        _recheck(caller, service.store, actor)
        raise error
    context["committed"] = True
    return model


def _record_command(event, actor, caller, written):
    """The command's own event, in the page's transaction.

    ``admin_census`` records its events the same way, inline; it is left
    unchanged here because an open pull request (#1443) edits it.
    """
    from .audit.schemas import ActorKind, Outcome
    from .audit.services import record_action

    record_action(
        event,
        actor_kind=ActorKind.PORTAL_USER,
        actor_id=actor.identity,
        subject_id=caller.automation_session_id,
        context={"outcome": Outcome.SUCCEEDED},
    )
    written.append(True)


@dataclass(frozen=True)
class FollowupUpdate(ReadModel):
    """The revision a ``followup update`` recorded, or found for its key.

    ``revision`` is the immutable record (``id``, ``request_id``,
    ``expected_version``, ``state``, ``outcome``, ``contact_channel``,
    ``contact_at``, ``created_at``), never its notes; ``request`` is the
    request as the command's transaction left it (``id``, ``version``,
    ``state``, ``outcome``).
    """

    created: bool
    request_key: UUID
    revision: dict
    request: dict


# A revision's members a document prints: never its notes or contact notes.
REVISION_FIELDS = (
    "id",
    "request_id",
    "expected_version",
    "state",
    "outcome",
    "contact_channel",
    "contact_at",
    "created_at",
)


def update_followup(
    caller,
    service,
    request_id,
    *,
    expected_version,
    request_key,
    state,
    outcome,
    notes,
    channel,
    contact_at,
    contact_notes,
    context,
):
    """``followup update``: a request page's Save.

    ``notes`` None keeps the request's current notes, as the page's form
    opens with them filled in; a revision stores the complete resulting
    workflow, so leaving them out must not clear them. The request is
    checked first (``_admitted``), then the lengths, then the page's form
    (``change_values``) and service (``update_request``), which record the
    page's ``ministry_request_updated``. The key binds the request's
    revision: a repeat with the same key and change returns it and records
    nothing, and the key with another change is ``invalid``. The command
    records ``admin_cmd_followup_update`` with each new revision.
    """
    from .audit.schemas import Action
    from .workflows.followup import FollowupRefusal, _latest_notes, update_request
    from .workflows.models import MinistryWorkflowRevision

    def write(actor, written, replayed):
        """The page's Save, with the command's event for a new revision."""
        repeat = request_id in replayed
        check_lengths(notes=notes, contact_notes=contact_notes)
        kept = _latest_notes([request_id])[request_id] if notes is None else notes
        change = change_values(
            state=state,
            outcome=outcome,
            notes=kept,
            channel=channel,
            contact_at=contact_at,
            contact_notes=contact_notes,
        )
        try:
            revision = update_request(
                service.store,
                actor.identity,
                request_id,
                expected_version=expected_version,
                request_key=request_key,
                change=change,
            )
        except FollowupRefusal as refusal:
            raise refused(refusal) from None
        if not repeat:
            _record_command(Action.ADMIN_CMD_FOLLOWUP_UPDATE, actor, caller, written)
        record = MinistryWorkflowRevision.objects.values(*REVISION_FIELDS).get(
            pk=revision.pk
        )
        [request] = _requests_now([request_id])
        return FollowupUpdate(
            created=not repeat,
            request_key=request_key,
            revision=record,
            request=request,
        )

    return _followup_write(
        caller,
        service,
        {request_id: expected_version},
        {request_id: request_key},
        request_key=request_key,
        context=context,
        write=write,
    )


@dataclass(frozen=True)
class FollowupBulk(ReadModel):
    """The requests a ``followup bulk`` resolved, or found for its key.

    ``outcome`` is the page's ``kind:outcome`` value; ``changed`` counts
    the requests this run resolved (0 for a repeat) and ``requests`` lists
    each chosen request as the transaction left it (``id``, ``version``,
    ``state``, ``outcome``), in id order.
    """

    created: bool
    request_key: UUID
    outcome: str
    changed: int
    requests: list


def kind_refused(refusal):
    """The bulk outcome's refusal of a request of the other kind.

    The dialog names the row by its Member; the command names the request
    by its id instead, in the page's words otherwise.
    """
    from .reports.ministries import outcome_label
    from .reports.ministry_followup_views import ACTIONS

    details = refusal.details
    action = ACTIONS[details["action"]]
    outcome = outcome_label(details["outcome"], details["kind"])
    message = (
        f"{details['request_id']} is a request to {action}, so {outcome} "
        "doesn't apply to it."
    )
    return InvalidFollowup([problem("request", message)])


def bulk_followup(
    caller,
    service,
    versions,
    *,
    action,
    outcome,
    request_key,
    channel,
    contact_at,
    contact_notes,
    context,
):
    """``followup bulk``: the queue's bulk outcome, all or none (#1131).

    ``versions`` maps each chosen request to the version its row showed.
    The page's service (``apply_bulk_outcome``) resolves each with its own
    revision and ``ministry_request_updated``, keeps each request's notes,
    and records ``ministry_followup_bulk_applied``. As on the page, the key
    binds each request, not the choice: each request's revision is keyed by
    ``uuid5(request_key, id)``, so a repeat replays those already recorded,
    and the same key with more requests resolves only the new ones (and
    records the events again for them). A request of the other kind is
    ``invalid``; one that changed, closed or whose Member left ParishSoft is
    ``stale_version``; nothing is saved then. The command records
    ``admin_cmd_followup_bulk`` with each run that resolved a request.
    """
    from .audit.schemas import Action
    from .workflows.followup import FollowupRefusal, apply_bulk_outcome, bulk_key_for

    def write(actor, written, replayed):
        """The page's bulk outcome, with the command's event when it changed."""
        check_lengths(contact_notes=contact_notes, requests=versions)
        # The state, outcome and notes are set per request by the service,
        # as the page's dialog leaves them to it.
        change = _contact_change(
            state="new",
            outcome=None,
            notes="",
            channel=channel,
            contact_at=contact_at,
            contact_notes=contact_notes,
        )
        try:
            revisions = apply_bulk_outcome(
                service.store,
                actor.identity,
                versions,
                action=action,
                outcome=outcome,
                bulk_key=request_key,
                change=change,
            )
        except FollowupRefusal as refusal:
            if refusal.code == "outcome_kind" and "request_id" in refusal.details:
                raise kind_refused(refusal) from None
            raise refused(refusal) from None
        changed = len(revisions) - len(replayed)
        if changed:
            _record_command(Action.ADMIN_CMD_FOLLOWUP_BULK, actor, caller, written)
        return FollowupBulk(
            created=bool(changed),
            request_key=request_key,
            outcome=f"{action}:{outcome}",
            changed=changed,
            requests=_requests_now(list(versions)),
        )

    return _followup_write(
        caller,
        service,
        versions,
        {key: bulk_key_for(request_key, key) for key in versions},
        request_key=request_key,
        context=context,
        write=write,
    )
