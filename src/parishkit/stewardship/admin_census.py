"""``report census``: the Census changes worklist as counts (ADM-11).

The page (``admin:census_changes``, ``reports.census_change_views``) lists
the census changes Families reported, which Admin and Staff carry into
ParishSoft (#528). The command reads the same list through the page's own
pieces, so the two cannot drift: its admission
(``census_change_views._principal``: the census capability, never a
Ministry leader), the shared report read (``admin_report_reads._report``:
the current campaign, ``admit_report_read`` and the campaign read guard),
its filters (``CensusQuery``, without the search) and its read
(``census_changes``), which words and filters the rows as the page does.

Each row names a Family and often a Member, and carries their contact
details, so the command prints counts only: how many rows the filters
keep, split by how they reach ParishSoft and by kind of change, and the
page's summary of every status. The rows themselves stay on the page and
its downloads (a later ``export census``). It records the page's
``census_changes_viewed``; as the other report reads, the event's
``count`` is the rows the command displayed, which is none.

``census resolve`` is a By-hand row's outcome (Entered in ParishSoft, Ignore
or Reopen, #528), through the page's own admission and the page's own
service (``census_resolution.resolve_census_change``), keyed by the page's
request key. ``census review`` is an Automatic row's review (Approve,
Ignore or Back to To review, ADM-09), the same way, through the page's
``census_review.review_census_change``. ``census edit`` is the review's
Edit value and Clear edit (ADM-09.04), through ``edit_census_change``, and
``census resolve-conflict`` its Use the Family's value anyway (ADM-09.03),
through ``resolve_census_conflict`` (#1434). All print ids, versions and
states, never the note, the Family, the Member or the value.
"""

from dataclasses import dataclass
from uuid import UUID

from django.db import DatabaseError, IntegrityError

from .admin_reads import NotAvailable, ReadModel, _held, _recheck


@dataclass(frozen=True)
class CensusReport(ReadModel):
    """The Census changes list of the current campaign, as counts.

    ``filters`` are the page's choices as it re-submits them (``status``,
    ``history``, ``route``, ``kind``, ``start``, ``end`` and ``zone``),
    without the search. ``matching`` is the rows the filters keep;
    ``routes`` splits them into ``automatic`` and ``by_hand`` and ``kinds``
    by kind of change. ``statuses`` is the page's summary: every row of the
    campaign by status key, whatever the other filters, with the history
    statuses (``superseded``, ``cancelled``) only when ``history`` is on.
    """

    campaign_id: UUID
    filters: dict
    matching: int
    routes: dict
    kinds: dict
    statuses: dict


def census_model(campaign_id, query, result):
    """``census_changes``'s result as ``report census`` prints it: counts only.

    No Family's or Member's name, DUID or value, and no resolver or note,
    reaches the document. The page's summary is a list of (wording, count)
    pairs with most zero counts left out; it is printed by status key, every
    status the summary covers, zeros included.
    """
    from .reports.census_changes import HISTORY, KINDS, STATUSES

    rows = result["rows"]
    summary = dict(result["summary"])
    history = bool(query.history)
    return CensusReport(
        campaign_id=campaign_id,
        filters={
            key: value for key, value in query.form_values().items() if key != "search"
        },
        matching=len(rows),
        routes={
            "automatic": sum(bool(row["automatic"]) for row in rows),
            "by_hand": sum(not row["automatic"] for row in rows),
        },
        # The page's Kind filter choices, less its "any" (Every kind).
        kinds={kind: sum(row["kind"] == kind for row in rows) for kind in KINDS[1:]},
        statuses={
            key: summary.get(wording, 0)
            for key, wording in STATUSES.items()
            if history or key not in HISTORY
        },
    )


def read_census(caller, service, filters):
    """``report census``: the Census changes list, counted.

    ``filters`` are the page's non-identifying filters (``status``,
    ``history``, ``route``, ``kind``, ``start``, ``end`` and the ``zone``
    the days fall in, required with them; None when not given), parsed by
    the page's ``CensusQuery`` once the reader is admitted, so a refusal
    (``invalid``) is reported after the session recheck and an ended
    session is exit 5. Admission, the campaign check and the guarded read
    are the shared report read's (``_report``), so no current campaign is
    ``not_available`` and a reader without the census capability (a
    Ministry leader) is ``denied``. Records ``census_changes_viewed``
    (succeeded or failed, with ``count`` 0) as the page records its view.
    """
    from .admin_report_reads import _report
    from .reports.census_change_views import _audit, _principal
    from .reports.census_changes import CensusQuery, census_changes

    given = {key: value for key, value in filters.items() if value is not None}
    holder = {}

    def admit(actor, campaign_id):
        """The page's filter check, before anything is recorded."""
        try:
            holder["query"] = CensusQuery.parse(given)
        except ValueError:
            _recheck(caller, service.store, actor)
            raise

    def read(guard, actor, campaign_id):
        """The page's read, reduced to its counts."""
        result = census_changes(campaign_id, holder["query"], actor)
        return census_model(campaign_id, holder["query"], result), None

    def audit(actor, campaign_id, outcome, values):
        """The page's view event: the command displayed no row."""
        from .audit.schemas import Action

        _audit(Action.CENSUS_CHANGES_VIEWED, actor, campaign_id, outcome, 0)

    return _report(
        caller, service, principal=_principal, read=read, audit=audit, admit=admit
    )


@dataclass(frozen=True)
class CensusResolve(ReadModel):
    """The resolution a ``census resolve`` recorded, or found for its key.

    ``resolution`` is the immutable record (``id``, ``proposal_id``,
    ``action``, ``expected_version``, ``created_at``), never its note;
    ``proposal`` is the change's state as the command's transaction left it
    (``id``, ``version``, ``decision``, ``execution``).
    """

    created: bool
    request_key: UUID
    resolution: dict
    proposal: dict


def racing_write(error):
    """Whether ``error`` is the page's racing second resolution.

    A second resolution of the same version is a unique violation, raised at
    its insert, so nothing committed; the page reports it as stale, and so
    does the command. Any other database refusal (a guard or audit
    mismatch) is not dressed up as the race.
    """
    from .reports.census_change_views import UNIQUE_VIOLATION

    return (
        isinstance(error, IntegrityError)
        and getattr(error.__cause__, "sqlstate", None) == UNIQUE_VIOLATION
    )


def _admit_resolve(caller, store, *, final=False):
    """The page's admission of a resolution post, or refuse.

    The page's own ``_principal`` (the census capability, never a Ministry
    leader), recording activity as its post does. ``command_scope`` repeats
    it (``final``) inside the transaction as a read-only recheck that writes
    nothing. A session that ended is exit 5 (``session_ended``), not the
    page's refusal.
    """
    from .accounts.automation_sessions import SessionUnusable
    from .accounts.sessions import authenticated_admin
    from .reports.census_change_views import _principal

    try:
        return _principal(caller, store, read_only=final)
    except PermissionError:
        if authenticated_admin(caller, store=store, read_only=True) is None:
            raise SessionUnusable("session_ended") from None
        raise


def _census_write(
    caller, service, proposal_id, *, request_key, context, records, write, event, build
):
    """One row write of the Census changes page, in the page's command scope.

    ``census resolve``, ``census review``, ``census edit`` and ``census
    resolve-conflict`` share it, as the page's one post handles them all.
    Admits as the page's post does, then in the page's command scope (the
    session row locked, the admission repeated around the effect) checks the
    current campaign as the page does (``admit_report_read``) and finds the
    Live change in it (another campaign's, or an unknown one, is
    ``not_available``, as the page refuses it). ``write(actor)`` is then
    the page's own service, which checks the capability, the version and
    the row's state and records the page's ``census_change_updated``; it
    returns the receipt. ``event``, the
    command's own action, joins the same transaction only when the key is
    new (no row of ``records``, the receipt's model, holds it): a repeat
    with the same intent returns the first receipt (``created`` false),
    another intent is ``invalid``. The campaign is checked first, so a
    repeat after the current campaign changed is ``not_available``.
    ``build(created, receipt, proposal)`` reads the document back inside
    the transaction, with ``proposal`` the change's state as the command
    leaves it. A ``write`` that changed nothing returns None: nothing is
    recorded, the command's event included, and ``created`` is false.

    A change updated since the version is ``stale_version``, as is the
    page's racing second write (a unique violation).
    ``context["request_id"]`` is the key, so an exit-6 document names it;
    ``context["committed"]`` is set once the write may have committed.
    """
    from .accounts.policy import Capability
    from .admin_report_reads import _current, _refusal, _refusals
    from .audit.schemas import ActorKind, Outcome
    from .audit.services import record_action
    from .campaigns.work_locks import require_work_order
    from .jobs.task_retries import command_scope
    from .observability import _guard_refusal
    from .reports.read_admission import admit_report_read
    from .responses.models import ProposedChange
    from .storage import StaleRecordError

    actor = _admit_resolve(caller, service.store)
    context["request_id"] = str(request_key)
    written = []

    def step():
        """The page's write in its command scope, with the command's event."""
        # A step repeated after an activating change starts over: nothing
        # from an earlier attempt, which rolled back, counts as written.
        written.clear()
        with command_scope(caller, service, actor, admit=_admit_resolve):
            campaign_id = _current()
            try:
                admit_report_read(campaign_id)
            except _refusals() as error:
                _refusal(caller, service, actor, error)
            # The page's own lookup: a Live change of this campaign.
            if (
                not ProposedChange.objects.filter(
                    pk=proposal_id, submission__campaign_id=campaign_id
                )
                .filter(submission__mode="live")
                .exists()
            ):
                raise NotAvailable("No such census change.")
            # The work-order lock the scope holds serializes concurrent
            # repeats, so this tells a repeat from a new key; fail closed if
            # the scope ever stops holding it (#1222, #147).
            require_work_order()
            repeat = records.objects.filter(
                actor_id=actor.identity, request_key=request_key
            ).exists()
            receipt = write(actor)
            # No receipt is a write that changed nothing (an unchanged edit):
            # nothing was recorded, so neither is the command's event.
            if not repeat and receipt is not None:
                record_action(
                    event,
                    actor_kind=ActorKind.PORTAL_USER,
                    actor_id=actor.identity,
                    subject_id=caller.automation_session_id,
                    context={"outcome": Outcome.SUCCEEDED},
                )
                written.append(True)
            # Read back inside the transaction: the database's creation
            # time, and the change's state as this command leaves it.
            proposal = ProposedChange.objects.values(
                "id", "version", "decision", "execution"
            ).get(pk=proposal_id)
            return build(not repeat and receipt is not None, receipt, proposal)

    try:
        model = _held(step)
    except DatabaseError as error:
        if racing_write(error):
            raise StaleRecordError("The census change was updated first.") from None
        if written and not _guard_refusal(error):
            context["committed"] = True
        raise
    except (NotAvailable, PermissionError) as error:
        # A session that ended meanwhile is exit 5, not the refusal.
        _recheck(caller, service.store, actor, Capability.VIEW_CENSUS)
        raise error
    context["committed"] = True
    return model


def resolve_census(
    caller,
    service,
    proposal_id,
    *,
    action,
    expected_version,
    note,
    request_key,
    context,
):
    """``census resolve``: a Census changes row's outcome.

    The page's ``resolve_census_change`` through ``_census_write``: it
    checks the action's capability (Reopen is the Administrator's), the
    version and the row's state. The command records
    ``admin_cmd_census_resolve``. An action the row's state does not allow,
    or a Reopen without a note, is ``invalid`` (the page's 400).
    """
    from .audit.schemas import Action
    from .responses.census_resolution import resolve_census_change
    from .responses.models import ProposalResolution

    def write(actor):
        """The page's row action."""
        return resolve_census_change(
            service.store,
            actor.identity,
            proposal_id,
            expected_version=expected_version,
            request_key=request_key,
            action=action,
            note=note,
        )

    def build(created, receipt, proposal):
        """The resolution as recorded, and the change's state."""
        resolution = ProposalResolution.objects.values(
            "id", "proposal_id", "action", "expected_version", "created_at"
        ).get(pk=receipt.pk)
        return CensusResolve(
            created=created,
            request_key=request_key,
            resolution=resolution,
            proposal=proposal,
        )

    return _census_write(
        caller,
        service,
        proposal_id,
        request_key=request_key,
        context=context,
        records=ProposalResolution,
        write=write,
        event=Action.ADMIN_CMD_CENSUS_RESOLVE,
        build=build,
    )


@dataclass(frozen=True)
class CensusReview(ReadModel):
    """The review a ``census review`` recorded, or found for its key.

    ``review`` is the immutable record (``id``, ``proposal_id``,
    ``decision``, ``expected_version``, ``created_at``), never its note;
    ``proposal`` is the change's state as the command's transaction left it
    (``id``, ``version``, ``decision``, ``execution``).
    """

    created: bool
    request_key: UUID
    review: dict
    proposal: dict


def review_census(
    caller,
    service,
    proposal_id,
    *,
    decision,
    expected_version,
    note,
    request_key,
    context,
):
    """``census review``: an Automatic row's review (ADM-09, #528).

    The page's ``review_census_change`` through ``_census_write``, so every
    guard is the page's: an Administrator only (``PUBLISH_CENSUS``; Staff
    are ``denied``), an Automatic change only, only while it is open
    (``pending``, ``conflict`` or ``failed``: never ``queued`` for
    publication, #1245 review note 5, nor final), a decision different
    from the current one, and no Approve of a Conflict. Any of those is
    ``invalid`` (the page's 400). The command records
    ``admin_cmd_census_review``.
    """
    from .audit.schemas import Action
    from .responses.census_review import review_census_change
    from .responses.models import ProposalReview

    def write(actor):
        """The page's Review."""
        return review_census_change(
            service.store,
            actor.identity,
            proposal_id,
            expected_version=expected_version,
            request_key=request_key,
            decision=decision,
            note=note,
        )

    def build(created, receipt, proposal):
        """The review as recorded, and the change's state."""
        review = ProposalReview.objects.values(
            "id", "proposal_id", "decision", "expected_version", "created_at"
        ).get(pk=receipt.pk)
        return CensusReview(
            created=created, request_key=request_key, review=review, proposal=proposal
        )

    return _census_write(
        caller,
        service,
        proposal_id,
        request_key=request_key,
        context=context,
        records=ProposalReview,
        write=write,
        event=Action.ADMIN_CMD_CENSUS_REVIEW,
        build=build,
    )


@dataclass(frozen=True)
class CensusEdit(ReadModel):
    """The edit a ``census edit`` recorded, or found for its key.

    ``review`` is the immutable record (``id``, ``proposal_id``, ``edited``:
    whether it sets an edited value, ``expected_version``, ``created_at``),
    never its value or note; None when the edit would change nothing
    (``unchanged``), so nothing was recorded. ``proposal`` is the change's
    state as the command's transaction left it (``id``, ``version``,
    ``decision``, ``execution`` and ``edited``, whether an edited value is
    now set).
    """

    created: bool
    unchanged: bool
    request_key: UUID
    review: dict | None
    proposal: dict


def edit_census(
    caller,
    service,
    proposal_id,
    *,
    value,
    expected_version,
    note,
    request_key,
    context,
):
    """``census edit``: an Automatic row's Edit value, or Clear edit (ADM-09.04).

    The page's ``edit_census_change`` through ``_census_write``, so every
    guard is the page's: an Administrator only, an Automatic Member change
    only, only while it is open (never ``queued`` nor final), and a value
    read as the Family form reads its own answer that fits ParishSoft's
    request schema. ``value`` is the raw text, or None to clear the edit. A
    value the change cannot take is ``invalid`` with the service's static
    reason in ``error.fields`` (field ``value``), never the value itself.
    An edit that would change nothing (the value already set, a clear with
    no edit set, the Family's own answer with no edit set) records nothing
    and is reported as ``unchanged``. The command records
    ``admin_cmd_census_edit``.
    """
    from .admin_changes import InvalidChange
    from .audit.schemas import Action
    from .responses.census_review import InvalidEdit, Unchanged, edit_census_change
    from .responses.models import ProposalReview, ProposedChange

    def write(actor):
        """The page's Edit value or Clear edit; None when nothing changes."""
        try:
            return edit_census_change(
                service.store,
                actor.identity,
                proposal_id,
                expected_version=expected_version,
                request_key=request_key,
                value=value,
                note=note,
            )
        except Unchanged:
            return None
        except InvalidEdit as error:
            # The service's message is static and never holds the value.
            raise InvalidChange(
                [{"field": "value", "code": "invalid", "message": str(error)}]
            ) from None

    def build(created, receipt, proposal):
        """The edit as recorded (if any), and the change's state."""
        review = None
        if receipt is not None:
            review = ProposalReview.objects.values(
                "id", "proposal_id", "expected_version", "created_at"
            ).get(pk=receipt.pk) | {"edited": receipt.admin_value_set}
        edited = ProposedChange.objects.values_list("admin_value_set", flat=True).get(
            pk=proposal_id
        )
        return CensusEdit(
            created=created,
            unchanged=receipt is None,
            request_key=request_key,
            review=review,
            proposal=proposal | {"edited": edited},
        )

    return _census_write(
        caller,
        service,
        proposal_id,
        request_key=request_key,
        context=context,
        records=ProposalReview,
        write=write,
        event=Action.ADMIN_CMD_CENSUS_EDIT,
        build=build,
    )


def resolve_conflict(
    caller,
    service,
    proposal_id,
    *,
    expected_version,
    note,
    request_key,
    context,
):
    """``census resolve-conflict``: Use the Family's value anyway (ADM-09.03).

    The page's ``census_review.resolve_census_conflict`` through
    ``_census_write``, so every guard is the page's: an Administrator only,
    an Automatic Member change in ``conflict`` whose ParishSoft value is
    known, one change at a time. It accepts ParishSoft's current value as
    the base, approves the change and makes it ``pending``; anything else is
    ``invalid``. The document is ``census review``'s (``CensusReview``); the
    command records ``admin_cmd_census_resolve_conflict``. Undoing it is
    ``census review --decision ignored|unreviewed``, which puts the change
    back to ``conflict``.
    """
    from .audit.schemas import Action
    from .responses.census_review import resolve_census_conflict
    from .responses.models import ProposalReview

    def write(actor):
        """The page's Use the Family's value anyway."""
        return resolve_census_conflict(
            service.store,
            actor.identity,
            proposal_id,
            expected_version=expected_version,
            request_key=request_key,
            note=note,
        )

    def build(created, receipt, proposal):
        """The resolution's review as recorded, and the change's state."""
        review = ProposalReview.objects.values(
            "id", "proposal_id", "decision", "expected_version", "created_at"
        ).get(pk=receipt.pk)
        return CensusReview(
            created=created, request_key=request_key, review=review, proposal=proposal
        )

    return _census_write(
        caller,
        service,
        proposal_id,
        request_key=request_key,
        context=context,
        records=ProposalReview,
        write=write,
        event=Action.ADMIN_CMD_CENSUS_RESOLVE_CONFLICT,
        build=build,
    )
