"""Source refresh commands of the Admin automation command line (ADM-11 PR 6a).

``refresh start`` asks for a manual full ParishSoft refresh as the Source
refresh page's confirmation does, through the same function
(``accounts.refresh_views.request_manual_refresh``), keyed by the page's
``request_key``: repeating a key returns the original refresh, and a key
crosses between the page and the command line. ``refresh status`` is the
page's read (``read_refresh_page``): what is running or waiting, and the
latest full and incremental outcomes.

The page asks for no fresh sign-in and no typed value, so neither command
prompts. ``refresh start`` admits as the page's form post does (``CONFIGURE``,
recording activity, so it needs a full-scope session) and runs inside the
delivery pages' command scope (``jobs.task_retries.command_scope``): the work
transaction, a lock on the command session's row, and the Administrator
rechecked before and after the effect. In that same transaction it records
one ``admin_cmd_refresh_start`` event, only when the key is new.
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from django.db import DatabaseError

from .admin_reads import ReadModel, Unavailable, _admit, _held, _recheck


def refresh_fields(refresh):
    """The full/incremental refresh facts ``status`` and ``refresh status`` share.

    ``refresh`` is ``source.refresh_status.full_refresh_status``'s result.
    """
    return {
        "full_succeeded_at": refresh.succeeded_at,
        "full_failed_at": refresh.failed_at,
        "full_failed_task_id": refresh.failed_task_id,
        "full_running": refresh.running,
        "delta_succeeded_at": refresh.delta_succeeded_at,
        "delta_failed_at": refresh.delta_failed_at,
        "frequency": refresh.frequency,
        "next_full_at": refresh.next_full_at,
        "delta_refresh": refresh.delta_refresh,
        # Data age and connection (#510): the newest promoted full refresh's
        # start, "data as of", the connection line, and the overdue full slot
        # with whether it is past the margin.
        "full_started_at": refresh.full_started_at,
        "data_as_of": refresh.data_as_of,
        "connection": None if refresh.connection is None else refresh.connection.state,
        "connection_at": None if refresh.connection is None else refresh.connection.at,
        "overdue_full_at": refresh.overdue_at,
        "out_of_date": refresh.out_of_date,
        "late_minutes": refresh.late_minutes,
        "held_for_send": refresh.held_for_send,
        # Held for a bulk send: when it runs at the latest, or that the
        # catch-up is running or about to (#510).
        "resume_at": refresh.resume_at,
        "catching_up": refresh.catching_up,
    }


@dataclass(frozen=True)
class RefreshStatus(ReadModel):
    """The Source refresh page's read.

    ``running`` is whether a refresh runs now; ``waiting`` whether a full
    refresh waits that a new request may join. ``source`` holds the same
    facts as ``status``'s ``source``, without ``refreshed_at``.
    """

    as_of: datetime
    running: bool
    waiting: bool
    lateness_minutes: int
    source: dict


def refresh_status_model(values, now):
    """The command's projection of ``refresh_views.read_refresh_page``'s values."""
    return RefreshStatus(
        as_of=now,
        running=values["pending"]["running"],
        waiting=values["pending"]["waiting"],
        lateness_minutes=values["lateness_minutes"],
        source=refresh_fields(values["full_refresh"]),
    )


def read_refresh_status(caller, service):
    """``refresh status``: the Source refresh page's read, for any session.

    It admits passively with the page's ``CONFIGURE`` capability and records
    no audit event, as the page's view records none. A restore under review
    or an unfinished setup is ``unavailable``, as the page answers 503.
    """
    from django.utils import timezone

    from .accounts.policy import Capability
    from .accounts.refresh_views import read_refresh_page
    from .campaigns.work_locks import read_transaction

    def step():
        """Admit, read in one snapshot, recheck."""
        actor = _admit(caller, service.store, Capability.CONFIGURE)
        with read_transaction():
            _configuration, values = read_refresh_page(service)
        _recheck(caller, service.store, actor, Capability.CONFIGURE)
        return refresh_status_model(values, timezone.now())

    return _held(step)


@dataclass(frozen=True)
class RefreshStart(ReadModel):
    """The refresh a ``refresh start`` requested, or found for its key.

    ``created`` is false when the key was already used (a repeat, from the
    command line or the page), which returns the original receipt. Follow
    the run with ``task show TASK_ROOT_ID --watch``.
    """

    created: bool
    request_key: UUID
    refresh: dict


def _admit_start(caller, store, *, final=False):
    """The page's admission of its form post: ``CONFIGURE``, recording activity.

    ``command_scope`` repeats it (``final``) inside the transaction as a
    read-only recheck that writes nothing, as ``background_principal`` does,
    so one command records the session's activity once. A session that
    ended is exit 5 (``session_ended``), not the page's refusal.
    """
    from .accounts.automation_sessions import SessionUnusable
    from .accounts.policy import Capability, allows
    from .accounts.sessions import authenticated_admin

    principal = authenticated_admin(
        caller, store=store, activity=not final, read_only=final
    )
    if principal is None:
        raise SessionUnusable("session_ended")
    if not allows(principal, Capability.CONFIGURE):
        raise PermissionError("A refresh needs the configure capability.")
    return principal


def start_refresh(caller, service, *, request_key, context):
    """``refresh start``: the Source refresh page's confirmation.

    A request that joins a full refresh already waiting is not an error: it
    returns that run's root, as the page's redirect does. A key already bound
    to another kind of refresh, or to another Administrator, is ``invalid``.
    The source refusals (another organization or scope, no configured
    organization) are ``unavailable``, as the page answers 503.

    ``context["request_id"]`` is the request key, so an exit-6 document names
    it. ``context["committed"]`` is set once the request may have committed:
    a database error counts only once the command and its event were
    written; before that the transaction rolled back and nothing changed.
    """
    from parishkit.config import ConfigError

    from .accounts.policy import Capability
    from .accounts.refresh_views import request_manual_refresh
    from .audit.schemas import Action, ActorKind, Outcome
    from .audit.services import record_action
    from .jobs.task_retries import command_scope
    from .observability import _guard_refusal
    from .source.errors import SourceOrganizationChanged, SourceScopeChanged
    from .source.refresh_models import SourceRefreshCommand
    from .storage import StorageInvariantError

    actor = _admit_start(caller, service.store)
    context["request_id"] = str(request_key)
    written = []

    def step():
        """Request inside the page's command scope, with the command's event."""
        # A step repeated after an activating change starts over: nothing
        # from an earlier attempt, which rolled back, counts as written.
        written.clear()
        with command_scope(caller, service, actor, admit=_admit_start):
            # The work-order lock the scope holds serializes concurrent
            # repeats, so this tells a repeat from a new key.
            repeat = SourceRefreshCommand.objects.filter(pk=request_key).exists()
            try:
                receipt = request_manual_refresh(caller, service, actor, request_key)
            except StorageInvariantError:
                raise ValueError(
                    "The request key was used for another refresh."
                ) from None
            if not repeat:
                record_action(
                    Action.ADMIN_CMD_REFRESH_START,
                    actor_kind=ActorKind.PORTAL_USER,
                    actor_id=actor.identity,
                    subject_id=caller.automation_session_id,
                    context={"outcome": Outcome.SUCCEEDED},
                )
                written.append(True)
        return RefreshStart(
            created=not repeat,
            request_key=request_key,
            refresh={
                "command_id": receipt.command_id,
                "request_id": receipt.request_id,
                "task_root_id": receipt.task_root_id,
            },
        )

    try:
        model = _held(step)
    except DatabaseError as error:
        if written and not _guard_refusal(error):
            context["committed"] = True
        raise
    except (SourceOrganizationChanged, SourceScopeChanged, ConfigError):
        # Checked before PermissionError: the source refusals subclass it.
        raise Unavailable("The configured ParishSoft source cannot be used.") from None
    except PermissionError as error:
        _recheck(caller, service.store, actor, Capability.CONFIGURE)
        raise error
    context["committed"] = True
    return model
