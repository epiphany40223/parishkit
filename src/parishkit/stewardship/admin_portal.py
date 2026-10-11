"""Family portal maintenance commands of the Admin command line (ADM-11 PR 7a).

``portal maintenance show`` and ``portal maintenance set`` are the Family
portal availability page (``accounts.family_maintenance_views``): its read
of the switch and its Close and Reopen buttons. Both go through the page's
own functions in ``accounts.family_maintenance``, whose state is the newest
of two audit events, so the command line and the page cannot drift.

``show`` admits passively (any session) with the page's ``CONFIGURE``
capability and records nothing, as the page's GET records nothing. It
returns the closing Administrator's id, not the email address the page
shows, as the reads leave out addresses.

``set`` is fresh-gated on the page. A full-scope automation session stands
in for the recent sign-in (``require_fresh``'s automation branch, which
records ``automation_fresh_gate`` and the ``fresh_gated`` notice), in the
same transaction as the page's ``set_closed`` and the command's
``admin_cmd_portal_maintenance_set``, so all commit or roll back together.
The switch has no version: asking for the state it is already in is the
page's refusal, reported as ``stale_version`` (nothing changed; read it
again), which is also what a repeat after an unknown outcome sees.
"""

from dataclasses import dataclass
from uuid import UUID

from django.db import DatabaseError

from .admin_reads import ReadModel, _held, _recheck


@dataclass(frozen=True)
class MaintenanceStatus(ReadModel):
    """Whether the Family portal is closed, since when, by whom, and the note.

    ``message`` is the Administrator's own bounded note that Families see
    (no email addresses; the page asks for no personal details).
    ``closed_by_id`` is the closing Administrator's portal user id.
    """

    closed: bool
    since: str | None
    message: str
    closed_by_id: UUID | None


def status_model(state):
    """The command's projection of ``family_maintenance.MaintenanceState``."""
    from .admin_report_reads import _instant

    return MaintenanceStatus(
        closed=state.closed,
        since=_instant(state.since),
        message=state.message,
        closed_by_id=state.actor_id,
    )


def read_maintenance(caller, service):
    """``portal maintenance show``: the page's read of the switch, any session."""
    from .accounts.family_maintenance import current_state
    from .accounts.policy import Capability
    from .admin_reads import _admit as admit_read
    from .campaigns.work_locks import read_transaction

    def step():
        """Admit, read the switch uncached as the page does, recheck."""
        actor = admit_read(caller, service.store, Capability.CONFIGURE)
        with read_transaction():
            state = current_state(cached=False)
        _recheck(caller, service.store, actor, Capability.CONFIGURE)
        return status_model(state)

    return _held(step)


def _admit(caller, service):
    """The page's form admission (``CONFIGURE``), recording activity as its POST.

    A session that ended is exit 5 (``session_ended``), not the page's
    refusal.
    """
    from .accounts.automation_sessions import SessionUnusable
    from .accounts.policy import Capability, allows
    from .accounts.sessions import authenticated_admin

    principal = authenticated_admin(caller, store=service.store, activity=True)
    if principal is None:
        raise SessionUnusable("session_ended")
    if not allows(principal, Capability.CONFIGURE):
        raise PermissionError("The portal switch needs the configure capability.")
    return principal


def set_maintenance(caller, service, *, closed, message, context):
    """``portal maintenance set``: the page's Close or Reopen button.

    ``message`` was already cleaned by the page's ``clean_message`` (and is
    empty for a reopening). The fresh gate, the page's ``set_closed`` (which
    records ``family_maintenance_started`` or ``_ended``) and the command's
    own event share one transaction. ``context["committed"]`` is set once
    the change may have committed (exit 6 then). Returns the switch as read
    after the commit.
    """
    from django.db import transaction

    from .accounts.family_maintenance import AlreadySet, current_state, set_closed
    from .accounts.policy import Capability
    from .accounts.sessions import require_fresh
    from .audit.schemas import Action, ActorKind, Outcome
    from .audit.services import record_action
    from .observability import _guard_refusal
    from .storage import StaleRecordError

    actor = _admit(caller, service)
    written = []
    try:
        with transaction.atomic():
            # The automation branch locks the session row for this
            # transaction and records the fresh-gate event and notice here.
            require_fresh(caller)
            try:
                set_closed(actor, closed=closed, message=message)
            except AlreadySet as error:
                raise StaleRecordError(str(error)) from None
            record_action(
                Action.ADMIN_CMD_PORTAL_MAINTENANCE_SET,
                actor_kind=ActorKind.PORTAL_USER,
                actor_id=actor.identity,
                subject_id=caller.automation_session_id,
                context={"outcome": Outcome.SUCCEEDED},
            )
            written.append(True)
    except DatabaseError as error:
        # A failure at the commit itself may have committed.
        if written and not _guard_refusal(error):
            context["committed"] = True
        raise
    except PermissionError:
        # A session ended meanwhile is exit 5, not the page's refusal.
        _recheck(caller, service.store, actor, Capability.CONFIGURE)
        raise
    context["committed"] = True
    return status_model(current_state(cached=False))
