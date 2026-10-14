"""The Integrations pages' small actions (ADM-11 PR 10).

``integration backup-probe`` is the Off-site backups page's **Test access**
and ``integration dismiss`` a key change line's **Dismiss**, through the
pages' own request-free pieces (``integration_views.queue_probe`` and
``integration_credentials.dismiss``), so the pages and the command line
cannot drift:

- ``integration backup-probe [--folder LINK]`` admits as the page's post
  does (``CONFIGURE``, recording activity, so it needs a full-scope
  session) and queues the page's check of the given (or saved) Google
  Drive folder, recording ``admin_cmd_integration_backup_probe`` in the
  check's transaction. The command never contacts Google: as for the
  page, the Google Workspace installer answers the check, and
  ``integration status backup --watch`` follows it.
- ``integration dismiss TARGET --request-id ID`` hides a finished key
  change's line as the page's Dismiss does, recording the page's
  ``credential_result_dismissed`` and ``admin_cmd_integration_dismiss``
  together, only when the line was dismissed.

Neither page asks for a recent sign-in or a confirmation, so neither
command is fresh-gated or prompts.
"""

from dataclasses import dataclass

from django.db import DatabaseError

from .admin_changes import _admit_change, _ended_or_raise
from .admin_reads import NotAvailable, ReadModel, _held, _recheck


@dataclass(frozen=True)
class BackupProbe(ReadModel):
    """``integration backup-probe``'s outcome.

    ``probe`` is the session Administrator's latest check as ``integration
    show backup`` prints it (now the queued one, unless it already
    finished), and ``pending`` whether it is still waiting for its answer.
    """

    pending: bool
    probe: dict | None


@dataclass(frozen=True)
class KeyDismissal(ReadModel):
    """``integration dismiss``'s outcome.

    ``dismissed`` is false when the request is not the finished line the
    page shows now (superseded, still running or already hidden): as the
    page's Dismiss, nothing then changes and nothing is recorded.
    """

    target: str
    request_id: str
    dismissed: bool


def _command_event(action, caller, actor):
    """Record the command's own event, attributed to the automation session."""
    from .audit.schemas import ActorKind, Outcome
    from .audit.services import record_action

    record_action(
        action,
        actor_kind=ActorKind.PORTAL_USER,
        actor_id=actor.identity,
        subject_id=caller.automation_session_id,
        context={"outcome": Outcome.SUCCEEDED},
    )


def _probe_problem(configuration, folder, error):
    """The page's Test access refusal as an ``invalid`` field problem.

    The field is told from the configuration, not the words: Google
    Workspace mail not set up is at ``google_workspace``; otherwise the
    folder link (none given and none saved, ``required``, or not a Drive
    folder) is at ``folder``. The message is the page's own.
    """
    from .accounts.integration_views import _records
    from .admin_changes import InvalidChange

    if "google_workspace" not in _records(configuration):
        field, code = "google_workspace", "required"
    else:
        field = "folder"
        code = "invalid" if (folder or "").strip() else "required"
    return InvalidChange([{"field": field, "code": code, "message": str(error)}])


def backup_probe(caller, service, *, folder, context):
    """``integration backup-probe``: queue the page's Test access check.

    Admits as the page's post does, reads the applied configuration (a
    restore under review or an activating change is ``unavailable``), then
    queues the check through the page's ``queue_probe`` with the command's
    event in one durable transaction. The page's refusals (the local
    environment, Workspace not set up, no folder, not a Drive folder link)
    are ``invalid``; another Administrator's Workspace change in between is
    ``stale_version``. ``folder`` is the page's folder link field, or None
    for the saved folder. Rechecks access before reporting.
    """
    from django.db import transaction

    from .accounts.admin_editing import editable_configuration
    from .accounts.integration_views import (
        _refuse_drive_in_local,
        backup_state,
        queue_probe,
    )
    from .accounts.policy import Capability
    from .admin_integrations import backup_documents
    from .audit.schemas import Action
    from .observability import _guard_refusal
    from .web.refusals import UserFacingError

    actor = _admit_change(caller, service)
    configuration = _held(lambda: editable_configuration(service))
    written = []
    # Refused before anything else, with no field, as ``integration preview``
    # refuses an off-site folder in the local environment.
    _refuse_drive_in_local()
    try:
        with transaction.atomic(durable=True):
            try:
                queue_probe(configuration, actor, folder or "")
            except UserFacingError as error:
                raise _probe_problem(configuration, folder, error) from None
            _command_event(Action.ADMIN_CMD_INTEGRATION_BACKUP_PROBE, caller, actor)
            written.append(True)
            # The page's status line for this Administrator, read in the
            # same transaction, so a later read failure cannot hide what
            # was queued.
            state = backup_state(configuration, actor.identity)
    except PermissionError as error:
        _ended_or_raise(caller, service, actor, error)
    except DatabaseError as error:
        if written and not _guard_refusal(error):
            context["committed"] = True
        raise
    context["committed"] = True
    _recheck(caller, service.store, actor, Capability.CONFIGURE)
    return BackupProbe(pending=state["pending"], probe=backup_documents(state)[1])


def dismiss_key(caller, service, target, *, request_id, context):
    """``integration dismiss``: the page's Dismiss of a finished key change.

    Admits as the page does, then calls the page's own ``dismiss`` against
    the applied record (or the stand-in of an optional integration not set
    up); a required integration that is not set up is ``not_available``.
    When the line is dismissed, the page's event and
    ``admin_cmd_integration_dismiss`` are recorded in one transaction.
    """
    from .accounts.admin_editing import editable_configuration
    from .accounts.integration_credentials import dismiss
    from .accounts.integration_views import _optional, _unset
    from .accounts.policy import Capability
    from .audit.schemas import Action
    from .observability import _guard_refusal

    actor = _admit_change(caller, service)
    configuration = _held(lambda: editable_configuration(service))
    try:
        record = _optional(configuration, target) or _unset(target)
    except LookupError:
        raise NotAvailable("That integration is not set up.") from None
    written = []

    def also():
        """The command's event, in the page's dismissal transaction."""
        _command_event(Action.ADMIN_CMD_INTEGRATION_DISMISS, caller, actor)
        written.append(True)

    try:
        dismissed = dismiss(
            target,
            record,
            request_id,
            actor_id=actor.identity,
            parish_id=configuration.active_configuration.parish.pk,
            record_more=also,
        )
    except PermissionError as error:
        _ended_or_raise(caller, service, actor, error)
    except DatabaseError as error:
        if written and not _guard_refusal(error):
            context["committed"] = True
        raise
    context["committed"] = dismissed
    _recheck(caller, service.store, actor, Capability.CONFIGURE)
    return KeyDismissal(target=target, request_id=str(request_id), dismissed=dismissed)
